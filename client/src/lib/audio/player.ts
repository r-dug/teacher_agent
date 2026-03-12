/**
 * Audio player: decode base64 float32 PCM → AudioContext playback queue.
 *
 * Chunks are serialised so they play sequentially without overlap.
 * Received chunks are also stored in an LRU buffer (last 10 turns) for
 * click-to-replay.
 */

const MAX_TURNS = 10

export class AudioPlayer {
  private ctx: AudioContext
  /** LRU: audioTurns[turnIdx][chunkIdx] = AudioBuffer */
  private audioTurns: Map<number, Map<number, AudioBuffer>> = new Map()
  /** Queue of pending [buffer, resolve] pairs */
  private playQueue: Array<[AudioBuffer, () => void]> = []
  private playing = false

  constructor() {
    this.ctx = new AudioContext()
  }

  /**
   * Decode a base64 float32 PCM chunk and enqueue it for playback.
   * Also stores the buffer for replay.
   */
  async enqueue(data: string, sampleRate: number, turnIdx: number, chunkIdx: number) {
    const pcm = _base64ToFloat32(data)
    const buffer = this.ctx.createBuffer(1, pcm.length, sampleRate)
    buffer.copyToChannel(pcm, 0)

    // Store for replay
    if (!this.audioTurns.has(turnIdx)) {
      this.audioTurns.set(turnIdx, new Map())
      this._evict()
    }
    this.audioTurns.get(turnIdx)!.set(chunkIdx, buffer)

    // Enqueue for sequential playback
    await new Promise<void>((resolve) => {
      this.playQueue.push([buffer, resolve])
      if (!this.playing) this._drainQueue()
    })
  }

  /** Replay all chunks of a stored turn in order. */
  async replay(turnIdx: number) {
    const chunks = this.audioTurns.get(turnIdx)
    if (!chunks) return
    // Resume suspended context (required after iOS user-gesture requirement).
    if (this.ctx.state === 'suspended') await this.ctx.resume()
    const sorted = [...chunks.entries()].sort(([a], [b]) => a - b)
    for (const [, buffer] of sorted) {
      await this._playBuffer(buffer)
    }
  }

  suspend() {
    return this.ctx.suspend()
  }

  resume() {
    return this.ctx.resume()
  }

  private async _drainQueue() {
    this.playing = true
    while (this.playQueue.length > 0) {
      const item = this.playQueue.shift()!
      await this._playBuffer(item[0])
      item[1]()    // resolve the enqueue() promise
    }
    this.playing = false
  }

  private _playBuffer(buffer: AudioBuffer): Promise<void> {
    return new Promise((resolve) => {
      const source = this.ctx.createBufferSource()
      source.buffer = buffer
      source.connect(this.ctx.destination)
      source.onended = () => resolve()
      source.start()
    })
  }

  /** Evict turns beyond MAX_TURNS (keep the most recent). */
  private _evict() {
    if (this.audioTurns.size <= MAX_TURNS) return
    const oldest = [...this.audioTurns.keys()].sort((a, b) => a - b)[0]!
    this.audioTurns.delete(oldest)
  }
}

function _base64ToFloat32(b64: string): Float32Array<ArrayBuffer> {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Float32Array(bytes.buffer)
}
