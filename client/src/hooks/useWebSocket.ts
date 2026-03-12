/**
 * Hook: manage a WsConnection for a given session + lesson.
 *
 * Returns the connection instance and its current status.
 * The connection is created once and cleaned up on unmount.
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { WsConnection, type WsOptions } from '@/lib/ws'
import type { ClientEvent } from '@/lib/types'

type WsStatus = 'connecting' | 'connected' | 'disconnected'

export function useWebSocket(
  opts: Pick<WsOptions, 'sessionId' | 'lessonId' | 'onEvent'> | null
) {
  const [status, setStatus] = useState<WsStatus>('disconnected')
  const connRef = useRef<WsConnection | null>(null)

  useEffect(() => {
    if (!opts) return
    // Defer by one tick so React StrictMode's mount→unmount→mount cycle
    // cancels the timer before a socket is created, avoiding a spurious
    // "closed before established" error on the first render.
    const timer = setTimeout(() => {
      const conn = new WsConnection({
        ...opts,
        onStatus: setStatus,
      })
      connRef.current = conn
      conn.connect()
    }, 0)
    return () => {
      clearTimeout(timer)
      connRef.current?.close()
      connRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts?.sessionId, opts?.lessonId])

  const send = useCallback((event: ClientEvent) => {
    connRef.current?.send(event)
  }, [])

  return { status, send }
}
