/**
 * Hook: manage the AudioPlayer instance.
 *
 * Returns { enqueue, replay } — stable references, safe to pass as callbacks.
 */

import { useRef, useCallback } from 'react'
import { AudioPlayer } from '@/lib/audio/player'

export function useAudioPlayer() {
  const playerRef = useRef<AudioPlayer | null>(null)

  function getPlayer() {
    if (!playerRef.current) playerRef.current = new AudioPlayer()
    return playerRef.current
  }

  const enqueue = useCallback(
    (data: string, sampleRate: number, turnIdx: number, chunkIdx: number) =>
      getPlayer().enqueue(data, sampleRate, turnIdx, chunkIdx),
    []
  )

  const replay = useCallback(
    (turnIdx: number) => getPlayer().replay(turnIdx),
    []
  )

  return { enqueue, replay }
}
