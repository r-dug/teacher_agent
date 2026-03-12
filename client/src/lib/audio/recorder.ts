/**
 * Recorder: getUserMedia → AudioWorklet (VAD) → base64 PCM utterances.
 *
 * Usage:
 *   const rec = new Recorder({ onUtterance, onSpeaking })
 *   await rec.start()
 *   rec.stop()
 */

import workletUrl from './vad.worklet.ts?worker&url'

export interface RecorderOptions {
  /** Called with base64-encoded float32 PCM + sample rate when an utterance ends. */
  onUtterance: (data: string, sampleRate: number) => void
  /** Called when VAD speech state changes. */
  onSpeaking?: (speaking: boolean) => void
}

export class Recorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private workletNode: AudioWorkletNode | null = null
  private opts: RecorderOptions

  constructor(opts: RecorderOptions) { this.opts = opts }

  async start() {
    // Initiate getUserMedia and AudioContext synchronously before any await so
    // that iOS Safari still considers this within the user-gesture activation
    // context (awaiting getUserMedia first would lose that context).
    const streamPromise = navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 16_000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    })
    this.ctx = new AudioContext({ sampleRate: 16_000 })
    if (this.ctx.state === 'suspended') await this.ctx.resume()
    await this.ctx.audioWorklet.addModule(workletUrl)
    this.stream = await streamPromise

    const source = this.ctx.createMediaStreamSource(this.stream)
    this.workletNode = new AudioWorkletNode(this.ctx, 'vad-processor')

    this.workletNode.port.onmessage = (ev: MessageEvent) => {
      const { type } = ev.data as { type: string }
      if (type === 'utterance') {
        const pcm = ev.data.pcm as Float32Array
        const b64 = _float32ToBase64(pcm)
        this.opts.onUtterance(b64, ev.data.sampleRate as number)
      } else if (type === 'speaking') {
        this.opts.onSpeaking?.(ev.data.value as boolean)
      }
    }

    source.connect(this.workletNode)
    // Don't connect workletNode to destination — we don't want local playback.
  }

  stop() {
    this.workletNode?.disconnect()
    this.workletNode = null
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    this.ctx?.close()
    this.ctx = null
  }

  get isActive() {
    return this.ctx !== null
  }
}

/** Encode Float32Array as base64. */
function _float32ToBase64(pcm: Float32Array): string {
  const bytes = new Uint8Array(pcm.buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]!)
  }
  return btoa(binary)
}
