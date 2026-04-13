/**
 * Memory Strategy Eval — LLM-driven student interacts with the teaching agent.
 *
 * The student agent reads the teacher's response and decides how to respond
 * naturally. It uses the same OpenAI API as the teaching agent.
 *
 * Usage:
 *   MEMORY_STRATEGY=rag_semantic TEST_LESSON_ID=<id> xvfb-run npx playwright test memory_strategy_eval
 *
 * Output: storage/evals/memory_eval_<strategy>_<timestamp>.jsonl
 */

import { test, expect } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'

const TEST_USER_EMAIL = process.env.TEST_USER_EMAIL || 'test@mail.com'
const TEST_USER_PASSWORD = process.env.TEST_USER_PASSWORD || 'aaaaaaaa'
const LESSON_ID = process.env.TEST_LESSON_ID || ''
const STRATEGY = process.env.MEMORY_STRATEGY || 'turn_summaries'
const NUM_TURNS = parseInt(process.env.EVAL_TURNS || '10', 10)
const STUDENT_MODEL = process.env.STUDENT_MODEL || 'gpt-4o-mini'

const STUDENT_SYSTEM = `You are a cooperative language student in a Japanese lesson.
You are genuinely trying to learn. Respond naturally and briefly (1-2 sentences).

Rules:
- Answer questions the teacher asks
- Attempt exercises when asked
- Say "yes" or "ok" when the teacher asks if you're ready
- If asked to write something, say what you would write (e.g. "I wrote: あいうえお")
- If quizzed, give your best answer
- Be slightly imperfect — make occasional small mistakes so the teacher has something to correct
- Keep responses short like a real voice conversation`

interface TurnRecord {
  turn_idx: number
  teacher_text: string
  student_input: string
  tool_calls: string[]
  latency_ms: number
  timestamp: number
}

/** Call OpenAI to generate a student response */
async function studentRespond(teacherText: string, history: string[]): Promise<string> {
  const apiKey = process.env.OPENAI_API_KEY
  if (!apiKey) return 'yes, I understand'

  const messages = [
    { role: 'system' as const, content: STUDENT_SYSTEM },
  ]

  // Include recent history for context
  for (const h of history.slice(-6)) {
    messages.push({ role: 'user' as const, content: h })
  }
  messages.push({ role: 'user' as const, content: `Teacher says: "${teacherText}"\n\nRespond as the student:` })

  try {
    const resp = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: STUDENT_MODEL,
        messages,
        max_tokens: 100,
        temperature: 0.7,
      }),
    })
    const data = await resp.json() as { choices?: Array<{ message?: { content?: string } }> }
    return data.choices?.[0]?.message?.content?.trim() || 'ok'
  } catch {
    return 'yes, I understand'
  }
}

/** Draw simple strokes on a canvas */
async function drawOnCanvas(page: import('@playwright/test').Page): Promise<boolean> {
  const canvas = page.locator('canvas').first()
  if (!await canvas.isVisible().catch(() => false)) return false

  const box = await canvas.boundingBox()
  if (!box) return false

  const cx = box.x + box.width / 2
  const cy = box.y + box.height / 2
  const r = Math.min(box.width, box.height) * 0.2

  // Draw a cross shape
  await page.mouse.move(cx - r, cy - r)
  await page.mouse.down()
  await page.mouse.move(cx + r, cy + r, { steps: 10 })
  await page.mouse.up()

  await page.mouse.move(cx + r, cy - r)
  await page.mouse.down()
  await page.mouse.move(cx - r, cy + r, { steps: 10 })
  await page.mouse.up()

  // Curved stroke
  await page.mouse.move(cx - r, cy)
  await page.mouse.down()
  await page.mouse.move(cx, cy - r * 0.8, { steps: 5 })
  await page.mouse.move(cx + r, cy, { steps: 5 })
  await page.mouse.up()

  return true
}

/** Handle any interactive tool overlay and return the tool name.
 *  Wrapped in try/catch so a single overlay failure doesn't crash the eval. */
async function handleOverlay(page: import('@playwright/test').Page): Promise<string | null> {
  try {
    await page.waitForTimeout(1000)

    // First: dismiss any blocking modal (ImageViewer, etc.)
    const closeBtn = page.locator('.fixed.inset-0 button[aria-label="Close image"], .fixed.inset-0 button:has-text("✕")').first()
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click({ force: true })
      await page.waitForTimeout(500)
    }

    // Sketchpad
    if (await page.locator('canvas').first().isVisible().catch(() => false)) {
      const drew = await drawOnCanvas(page)
      if (drew) {
        await page.waitForTimeout(500)
        const submit = page.locator('button:has-text("Submit")').first()
        if (await submit.isEnabled({ timeout: 3000 }).catch(() => false)) {
          await submit.click({ force: true })
          await page.waitForTimeout(4000)
          return 'open_sketchpad'
        }
      }
      const cancel = page.locator('button:has-text("Cancel")').first()
      if (await cancel.isVisible().catch(() => false)) {
        await cancel.click({ force: true })
        await page.waitForTimeout(1000)
      }
      return 'open_sketchpad_cancelled'
    }

    // Quiz — pick first choice
    const quizOption = page.locator('[data-testid="quiz-option"], .quiz-choice, button[role="radio"]').first()
    if (await quizOption.isVisible().catch(() => false)) {
      await quizOption.click({ force: true })
      const submit = page.locator('button:has-text("Submit")').first()
      if (await submit.isEnabled({ timeout: 3000 }).catch(() => false)) {
        await submit.click({ force: true })
        await page.waitForTimeout(3000)
        return 'show_quiz'
      }
    }

    // Flashcards
    const flipCard = page.locator('text=Tap to flip').first()
    if (await flipCard.isVisible().catch(() => false)) {
      for (let i = 0; i < 20; i++) {
        if (!await flipCard.isVisible().catch(() => false)) break
        await flipCard.click({ force: true })
        await page.waitForTimeout(500)
        const gotIt = page.locator('button:has-text("Got it")').first()
        const missed = page.locator('button:has-text("Missed")').first()
        if (i % 3 === 2 && await missed.isVisible().catch(() => false)) {
          await missed.click({ force: true })
        } else if (await gotIt.isVisible().catch(() => false)) {
          await gotIt.click({ force: true })
        }
        await page.waitForTimeout(300)
      }
      const done = page.locator('button:has-text("Submit"), button:has-text("Done")').first()
      if (await done.isVisible().catch(() => false)) {
        await done.click({ force: true })
        await page.waitForTimeout(3000)
      }
      return 'show_flashcard_deck'
    }

    // Fill in the blank
    const blankInput = page.locator('input[placeholder]').first()
    if (await blankInput.isVisible().catch(() => false)) {
      const inputs = page.locator('input[placeholder]')
      const count = await inputs.count()
      for (let i = 0; i < count; i++) {
        await inputs.nth(i).fill('answer')
      }
      await page.waitForTimeout(500)
      const submit = page.locator('button:has-text("Submit")').first()
      if (await submit.isEnabled({ timeout: 3000 }).catch(() => false)) {
        await submit.click({ force: true })
        await page.waitForTimeout(3000)
        return 'fill_in_the_blank'
      }
      const cancel = page.locator('button:has-text("Cancel")').first()
      if (await cancel.isVisible().catch(() => false)) {
        await cancel.click({ force: true })
        await page.waitForTimeout(1000)
      }
      return 'fill_in_the_blank_cancelled'
    }

    return null
  } catch (e) {
    console.log(`  ⚠️  Overlay handler error: ${String(e).slice(0, 80)}`)
    // Try to dismiss any remaining overlay
    try {
      await page.keyboard.press('Escape')
      await page.waitForTimeout(1000)
    } catch { /* ignore */ }
    return 'overlay_error'
  }
}

/** Get the latest teacher text from the conversation */
async function getLatestTeacherText(page: import('@playwright/test').Page): Promise<string> {
  // Get all assistant bubbles (left-aligned)
  const bubbles = page.locator('.justify-start .rounded-2xl')
  const count = await bubbles.count()
  if (count === 0) return ''

  const last = bubbles.nth(count - 1)
  try {
    return (await last.textContent({ timeout: 3000 }))?.trim() || ''
  } catch {
    return ''
  }
}

test.describe('Memory Strategy Eval', () => {
  test(`eval ${STRATEGY} with LLM student`, async ({ page, request, baseURL }) => {
    test.setTimeout(3_600_000) // 60 minutes
    if (!baseURL) throw new Error('baseURL is not set')
    if (!LESSON_ID) throw new Error('TEST_LESSON_ID env var required')

    // ── Login ──
    const loginResp = await request.post(`${baseURL}/api/auth/login`, {
      data: { email: TEST_USER_EMAIL, password: TEST_USER_PASSWORD },
    })
    expect(loginResp.ok()).toBeTruthy()
    const { session_id } = await loginResp.json()

    await page.goto(`${baseURL}/`)
    await page.evaluate((sid: string) => localStorage.setItem('session_id', sid), session_id)
    await page.reload()

    // ── Navigate to lesson ──
    await page.goto(`${baseURL}/teach/${LESSON_ID}`)
    console.log(`\n🎓 Starting eval: strategy=${STRATEGY}, lesson=${LESSON_ID}\n`)

    // Wait for lesson UI
    const inputBar = page.locator('textarea, input[placeholder*="Message"]').first()
    await expect(inputBar).toBeVisible({ timeout: 30_000 })

    // Wait for teacher's opening
    await page.waitForTimeout(8000)

    // Save results even on failure
    test.info().attachments.push({ name: 'eval-strategy', body: Buffer.from(STRATEGY), contentType: 'text/plain' })
    const _saveOnExit = () => { try { saveResults() } catch { /* ignore */ } }
    process.once('beforeExit', _saveOnExit)

    const turnRecords: TurnRecord[] = []
    const history: string[] = []

    for (let i = 0; i < NUM_TURNS; i++) {
      const turnStart = Date.now()

      // Check for overlay first
      const overlayTool = await handleOverlay(page)
      if (overlayTool) {
        console.log(`  📋 Turn ${i}: handled ${overlayTool}`)
        // Wait for teacher response after tool
        await page.waitForTimeout(6000)
      }

      // Read what teacher said
      const teacherText = await getLatestTeacherText(page)
      if (teacherText) {
        history.push(`Teacher: ${teacherText.slice(0, 200)}`)
      }

      // Generate student response via LLM
      const studentInput = await studentRespond(teacherText, history)
      history.push(`Student: ${studentInput}`)

      console.log(`  💬 Turn ${i}: Teacher: "${teacherText.slice(0, 60)}..."`)
      console.log(`             Student: "${studentInput}"`)

      // Wait for input to be enabled (agent not busy)
      try {
        await expect(inputBar).toBeEnabled({ timeout: 20_000 })
        await inputBar.fill(studentInput)
        await inputBar.press('Enter')
      } catch (e) {
        console.log(`  ⚠️  Failed to send (agent busy?): ${String(e).slice(0, 80)}`)
        continue
      }

      // Wait for teacher response
      await page.waitForTimeout(8000)

      // Check for new overlay after response
      const newOverlay = await handleOverlay(page)
      if (newOverlay) {
        console.log(`  📋 Turn ${i}: handled ${newOverlay} after response`)
        await page.waitForTimeout(5000)
      }

      const latencyMs = Date.now() - turnStart

      turnRecords.push({
        turn_idx: i,
        teacher_text: teacherText.slice(0, 500),
        student_input: studentInput,
        tool_calls: [overlayTool, newOverlay].filter(Boolean) as string[],
        latency_ms: latencyMs,
        timestamp: turnStart / 1000,
      })
    }

    saveResults()

    function saveResults() {
      if (turnRecords.length === 0) return
      const outputDir = path.resolve(import.meta.dirname ?? '.', '../../../../storage/evals')
      fs.mkdirSync(outputDir, { recursive: true })

      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      const outputPath = path.join(outputDir, `memory_eval_${STRATEGY}_${timestamp}.jsonl`)

      const lines = turnRecords.map((r) => JSON.stringify({
        ...r,
        strategy: STRATEGY,
        lesson_id: LESSON_ID,
        model: process.env.TEACH_LLM_MODEL || 'unknown',
        student_model: STUDENT_MODEL,
      }))
      fs.writeFileSync(outputPath, lines.join('\n') + '\n')

      console.log(`\n✅ Eval saved: ${outputPath}`)
      console.log(`   Turns: ${turnRecords.length}`)
      console.log(`   Tools used: ${turnRecords.reduce((s, r) => s + r.tool_calls.length, 0)}`)
      console.log(`   Avg latency: ${Math.round(turnRecords.reduce((s, r) => s + r.latency_ms, 0) / turnRecords.length)}ms`)
    }
  })
})
