/**
 * Hook: continuous microphone streaming for realtime voice conversations.
 *
 * Captures mono float32 PCM chunks and emits them every `chunkMs`.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

interface UseRealtimeStreamRecorderOptions {
  onChunk: (data: string, sampleRate: number) => void
}

const CHUNK_MS = 200
const SPEAKING_THRESHOLD = 0.012

export function useRealtimeStreamRecorder({ onChunk }: UseRealtimeStreamRecorderOptions) {
  const [isRecording, setIsRecording] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)

  const streamRef = useRef<MediaStream | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const sinkGainRef = useRef<GainNode | null>(null)
  const remainderRef = useRef<Float32Array>(new Float32Array(0))
  const speakingFramesRef = useRef(0)
  const silenceFramesRef = useRef(0)
  const runningRef = useRef(false)

  const emitChunk = useCallback((pcm: Float32Array, sampleRate: number) => {
    if (pcm.length === 0) return
    const bytes = new Uint8Array(pcm.buffer)
    let binary = ''
    const step = 0x8000
    for (let i = 0; i < bytes.length; i += step) {
      binary += String.fromCharCode(...bytes.subarray(i, i + step))
    }
    onChunk(btoa(binary), sampleRate)
  }, [onChunk])

  const stop = useCallback(async () => {
    runningRef.current = false

    const remainder = remainderRef.current
    const sampleRate = ctxRef.current?.sampleRate ?? 16000
    if (remainder.length > 0) {
      emitChunk(remainder, sampleRate)
    }
    remainderRef.current = new Float32Array(0)
    speakingFramesRef.current = 0
    silenceFramesRef.current = 0

    processorRef.current?.disconnect()
    sourceRef.current?.disconnect()
    sinkGainRef.current?.disconnect()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    sourceRef.current = null
    processorRef.current = null
    sinkGainRef.current = null

    const ctx = ctxRef.current
    ctxRef.current = null
    if (ctx) {
      try {
        await ctx.close()
      } catch {
        // ignore close races
      }
    }

    setIsSpeaking(false)
    setIsRecording(false)
  }, [emitChunk])

  const start = useCallback(async () => {
    if (runningRef.current) return

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    streamRef.current = stream

    const ctx = new AudioContext({ latencyHint: 'interactive' })
    ctxRef.current = ctx
    await ctx.resume()

    const source = ctx.createMediaStreamSource(stream)
    sourceRef.current = source

    const processor = ctx.createScriptProcessor(2048, 1, 1)
    processorRef.current = processor

    const sinkGain = ctx.createGain()
    sinkGain.gain.value = 0
    sinkGainRef.current = sinkGain

    const chunkSamples = Math.max(512, Math.floor((ctx.sampleRate * CHUNK_MS) / 1000))
    remainderRef.current = new Float32Array(0)
    speakingFramesRef.current = 0
    silenceFramesRef.current = 0

    processor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!runningRef.current) return
      const input = event.inputBuffer.getChannelData(0)
      const frame = new Float32Array(input.length)
      frame.set(input)

      let energy = 0
      for (let i = 0; i < frame.length; i += 1) {
        energy += frame[i]! * frame[i]!
      }
      const rms = Math.sqrt(energy / Math.max(1, frame.length))
      if (rms >= SPEAKING_THRESHOLD) {
        speakingFramesRef.current += 1
        silenceFramesRef.current = 0
      } else {
        silenceFramesRef.current += 1
        if (silenceFramesRef.current > 6) speakingFramesRef.current = 0
      }
      setIsSpeaking(speakingFramesRef.current > 1)

      const prior = remainderRef.current
      const merged = new Float32Array(prior.length + frame.length)
      merged.set(prior, 0)
      merged.set(frame, prior.length)

      let offset = 0
      while (offset + chunkSamples <= merged.length) {
        emitChunk(merged.slice(offset, offset + chunkSamples), ctx.sampleRate)
        offset += chunkSamples
      }
      remainderRef.current = merged.slice(offset)
    }

    source.connect(processor)
    processor.connect(sinkGain)
    sinkGain.connect(ctx.destination)

    runningRef.current = true
    setIsRecording(true)
    setIsSpeaking(false)
  }, [emitChunk])

  useEffect(() => {
    return () => {
      void stop()
    }
  }, [stop])

  return { isRecording, isSpeaking, start, stop }
}
