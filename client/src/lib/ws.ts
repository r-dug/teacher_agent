/**
 * WebSocket connection manager.
 *
 * Provides:
 *  - Typed send() with ClientEvent
 *  - Typed event listeners via on() / off()
 *  - Simple reconnect loop with exponential backoff
 */

import type { ClientEvent, ServerEvent } from './types'

type EventHandler = (event: ServerEvent) => void
type StatusHandler = (status: 'connecting' | 'connected' | 'disconnected') => void

export interface WsOptions {
  sessionId: string
  lessonId: string
  onEvent: EventHandler
  onStatus?: StatusHandler
  maxRetries?: number      // default: unlimited (-1)
  baseDelay?: number       // ms, default 500
  maxDelay?: number        // ms, default 8000
}

export class WsConnection {
  private ws: WebSocket | null = null
  private retries = 0
  private closed = false
  private retryTimer: ReturnType<typeof setTimeout> | null = null

  private opts: WsOptions
  constructor(opts: WsOptions) { this.opts = opts }

  connect() {
    if (this.closed) return
    // Use window.location.host in all environments.
    // In dev: Vite proxies /ws → wss://localhost:8000 with secure:false, so the
    // browser only sees the already-trusted :5173 cert.
    // In prod: BFF serves the page and handles /ws on the same host:port.
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws/${this.opts.sessionId}?lesson_id=${this.opts.lessonId}`
    this.opts.onStatus?.('connecting')
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onopen = () => {
      this.retries = 0
      this.opts.onStatus?.('connected')
    }

    ws.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data as string) as ServerEvent
        this.opts.onEvent(event)
      } catch {
        console.error('[WS] Failed to parse message:', ev.data)
      }
    }

    ws.onclose = () => {
      if (this.closed) return
      this.opts.onStatus?.('disconnected')
      this._scheduleReconnect()
    }

    ws.onerror = (err) => {
      console.error('[WS] Error:', err)
      // onclose fires after onerror, which handles reconnection
    }
  }

  send(event: ClientEvent) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(event))
    } else {
      console.warn('[WS] Cannot send — socket not open', event)
    }
  }

  close() {
    this.closed = true
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  get isOpen() {
    return this.ws?.readyState === WebSocket.OPEN
  }

  private _scheduleReconnect() {
    const { maxRetries = -1, baseDelay = 500, maxDelay = 8000 } = this.opts
    if (maxRetries !== -1 && this.retries >= maxRetries) return

    const delay = Math.min(baseDelay * 2 ** this.retries, maxDelay)
    this.retries++
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      this.connect()
    }, delay)
  }
}
