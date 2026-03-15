// @ts-nocheck
// AudioWorkletGlobalScope — DOM lib has no types for this environment.

/**
 * VAD processor: energy-based voice activity detection.
 *
 * Messages sent to main thread:
 *   { type: 'speaking', value: boolean }
 *   { type: 'utterance', pcm: Float32Array, sampleRate: number }
 *
 * Tuning:
 *   SPEECH_THRESHOLD  — RMS level that triggers speech start
 *   SILENCE_FRAMES    — consecutive silent frames before utterance is closed
 *   MIN_SPEECH_FRAMES — minimum speech frames to count as an utterance
 */

const SPEECH_THRESHOLD = 0.01
const SILENCE_FRAMES = 250  // ~2 s at 128 samples / 16 kHz
const MIN_SPEECH_FRAMES = 8 // ~160 ms — ignore tiny blips

class VadProcessor extends AudioWorkletProcessor {
  private _speaking = false
  private _silenceCount = 0
  private _speechCount = 0
  private _buf: Float32Array[] = []

  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0]?.[0]
    if (!input) return true

    // RMS energy of this frame
    let sum = 0
    for (let i = 0; i < input.length; i++) sum += input[i] * input[i]
    const rms = Math.sqrt(sum / input.length)

    const active = rms > SPEECH_THRESHOLD

    if (active) {
      this._silenceCount = 0
      this._speechCount++
      this._buf.push(input.slice())

      if (!this._speaking) {
        this._speaking = true
        this.port.postMessage({ type: 'speaking', value: true })
      }
    } else if (this._speaking) {
      this._silenceCount++
      this._buf.push(input.slice())  // buffer trailing silence too

      if (this._silenceCount >= SILENCE_FRAMES) {
        this._speaking = false
        this.port.postMessage({ type: 'speaking', value: false })

        if (this._speechCount >= MIN_SPEECH_FRAMES) {
          // Flatten buffer into a single Float32Array
          const totalLen = this._buf.reduce((n, b) => n + b.length, 0)
          const pcm = new Float32Array(totalLen)
          let offset = 0
          for (const chunk of this._buf) {
            pcm.set(chunk, offset)
            offset += chunk.length
          }
          this.port.postMessage({ type: 'utterance', pcm, sampleRate }, [pcm.buffer])
        }

        this._buf = []
        this._speechCount = 0
        this._silenceCount = 0
      }
    }

    return true
  }
}

registerProcessor('vad-processor', VadProcessor)
