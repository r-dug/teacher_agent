/**
 * Recorder: microphone → Silero VAD → base64 PCM utterances.
 *
 * Uses @ricky0123/vad-web (Silero VAD neural model) instead of energy-based
 * detection. End-of-utterance silence window is ~768 ms (was ~2 s).
 *
 * vad-web + onnxruntime-web are loaded as UMD globals via <script> tags in
 * index.html (/vad/ort.min.js then /vad/vad.bundle.min.js). VAD assets
 * (ONNX model, worklet, WASM) are served from /vad/ — see package.json
 * `copy-vad-assets` script which populates public/vad/.
 *
 * Usage:
 *   const rec = new Recorder({ onUtterance, onSpeaking })
 *   await rec.start()
 *   rec.stop()
 */

// MicVAD is loaded as a UMD global via /vad/vad.bundle.min.js in index.html.
type VadInstance = { start(): Promise<void>; destroy(): void }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const { MicVAD } = (self as any).vad as {
  MicVAD: { new: (opts: Record<string, unknown>) => Promise<VadInstance> }
}

export interface RecorderOptions {
  /** Called with base64-encoded float32 PCM (16 kHz) when an utterance ends. */
  onUtterance: (data: string, sampleRate: number) => void
  /** Called when VAD speech state changes. */
  onSpeaking?: (speaking: boolean) => void
}

export class Recorder {
  private vad: VadInstance | null = null
  private opts: RecorderOptions

  constructor(opts: RecorderOptions) { this.opts = opts }

  async start() {
    this.vad = await MicVAD.new({
      // Self-hosted assets in public/vad/ (populated by `npm run copy-vad-assets`).
      baseAssetPath:    '/vad/',
      onnxWASMBasePath: '/vad/',
      model: 'v5',

      // Silero VAD tuning.
      positiveSpeechThreshold: 0.5,   // confidence to enter speaking state
      negativeSpeechThreshold: 0.35,  // confidence to leave speaking state
      minSpeechMs:    288,            // ~3 frames minimum to count as speech
      preSpeechPadMs: 288,            // prepend ~3 frames before speech onset
      redemptionMs:   768,            // ~8 frames silence → utterance ends

      onSpeechStart: () => {
        this.opts.onSpeaking?.(true)
      },

      onSpeechEnd: (audio: Float32Array) => {
        this.opts.onSpeaking?.(false)
        this.opts.onUtterance(_float32ToBase64(audio), 16000)
      },

      // False positive: Silero changed its mind — reset speaking indicator.
      onVADMisfire: () => {
        this.opts.onSpeaking?.(false)
      },
    })

    await this.vad!.start()
  }

  stop() {
    this.vad?.destroy()
    this.vad = null
  }

  get isActive() {
    return this.vad !== null
  }
}

function _float32ToBase64(pcm: Float32Array): string {
  const bytes = new Uint8Array(pcm.buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]!)
  return btoa(binary)
}
