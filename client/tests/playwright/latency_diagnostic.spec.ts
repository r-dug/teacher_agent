/**
 * Latency diagnostic — measures timing of WebSocket events during a teaching turn.
 * Run with:
 *   E2E_BASE_URL=https://tutorail.app npx playwright test latency_diagnostic --headed
 */

import { test, expect } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL || 'https://tutorail.app'
const EMAIL = process.env.TEST_USER_EMAIL || 'test@mail.com'
const PASSWORD = process.env.TEST_USER_PASSWORD || 'aaaaaaaa'
const LESSON_ID = process.env.TEST_LESSON_ID || '69e12240-092f-4153-8896-d09b8a037858'

test('teaching turn latency diagnostic', async ({ page, request }) => {
  // ── Login ────────────────────────────────────────────────────────────────────
  const loginResp = await request.post(`${BASE_URL}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(loginResp.ok()).toBeTruthy()
  const { session_id } = await loginResp.json()

  await page.goto(`${BASE_URL}/`)
  await page.evaluate((sid) => localStorage.setItem('session_id', sid), session_id)
  await page.reload()

  // ── Intercept WebSocket messages for timing ──────────────────────────────────
  const events: { ts: number; dir: string; event: string; raw: string }[] = []
  const t0 = Date.now()

  page.on('websocket', (ws) => {
    console.log(`[WS] connected: ${ws.url()}`)

    ws.on('framesent', (frame) => {
      try {
        const data = JSON.parse(frame.payload as string)
        events.push({ ts: Date.now() - t0, dir: '→ client', event: data.event ?? '?', raw: frame.payload as string })
        console.log(`[WS →] +${Date.now() - t0}ms  ${data.event}`)
      } catch { /* binary frame */ }
    })

    ws.on('framereceived', (frame) => {
      try {
        const data = JSON.parse(frame.payload as string)
        const ts = Date.now() - t0
        events.push({ ts, dir: '← server', event: data.event ?? '?', raw: frame.payload as string })
        console.log(`[WS ←] +${ts}ms  ${data.event}`)
      } catch { /* binary audio frame */ }
    })

    ws.on('close', () => console.log('[WS] closed'))
  })

  // ── Navigate to lesson ───────────────────────────────────────────────────────
  await page.goto(`${BASE_URL}/teach/${LESSON_ID}`)
  await expect(page.locator('textarea')).toBeVisible({ timeout: 20000 })

  // Wait a moment for WS to connect and initial events to settle
  await page.waitForTimeout(3000)

  // ── Send a text message and time the response ────────────────────────────────
  const sendTs = Date.now() - t0
  console.log(`\n[TEST] Sending message at +${sendTs}ms`)

  const textarea = page.locator('textarea')
  await textarea.fill('What is the main topic of this lesson?')
  await textarea.press('Enter')

  // Wait for turn_complete (up to 60s)
  const turnCompletePromise = page.waitForFunction(
    () => (window as any).__lastWsEvent === 'turn_complete',
    { timeout: 60000 }
  ).catch(() => null)

  // Inject listener to track last event
  await page.evaluate(() => {
    const origWS = window.WebSocket
    ;(window as any).__lastWsEvent = null
  })

  // Just wait up to 60s for the conversation to show a response
  await page.waitForTimeout(60000).catch(() => {})

  // ── Print timing summary ─────────────────────────────────────────────────────
  console.log('\n════════════════════════════════════════')
  console.log('  WebSocket Event Timeline')
  console.log('════════════════════════════════════════')

  const keyEvents = ['text_message', 'turn_start', 'tts_playing', 'text_chunk',
                     'chunk_complete', 'turn_complete', 'error', 'decompose_complete',
                     'section_advanced', 'status']

  let firstChunk: number | null = null
  let firstAudio: number | null = null
  let turnComplete: number | null = null
  let turnStart: number | null = null

  for (const ev of events) {
    if (keyEvents.includes(ev.event)) {
      console.log(`  ${ev.dir.padEnd(10)} +${String(ev.ts).padStart(6)}ms  ${ev.event}`)
    }
    if (ev.event === 'turn_start' && turnStart === null) turnStart = ev.ts
    if (ev.event === 'text_chunk' && firstChunk === null) firstChunk = ev.ts
    if (ev.event === 'tts_playing' && firstAudio === null) firstAudio = ev.ts
    if (ev.event === 'turn_complete') turnComplete = ev.ts
  }

  console.log('\n════════════════════════════════════════')
  console.log('  Latency Breakdown')
  console.log('════════════════════════════════════════')
  if (turnStart !== null)
    console.log(`  Turn start received:      +${turnStart}ms after page load`)
  if (firstChunk !== null && turnStart !== null)
    console.log(`  First text chunk:         +${firstChunk - turnStart}ms after turn_start  ← LLM TTFT`)
  if (firstAudio !== null && firstChunk !== null)
    console.log(`  First audio (tts_playing):+${firstAudio - firstChunk}ms after first chunk  ← TTS latency`)
  if (turnComplete !== null && turnStart !== null)
    console.log(`  Turn complete:            +${turnComplete - turnStart}ms after turn_start  ← total turn`)

  // Check for errors
  const errors = events.filter(e => e.event === 'error')
  if (errors.length) {
    console.log('\n  ERRORS:')
    for (const e of errors) console.log('  ', e.raw)
  }

  console.log('════════════════════════════════════════\n')
})
