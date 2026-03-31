import { test, expect } from '@playwright/test'

const TEST_USER_EMAIL = process.env.TEST_USER_EMAIL || 'test@mail.com'
const TEST_USER_PASSWORD = process.env.TEST_USER_PASSWORD || 'aaaaaaaa'
const LESSON_ID = process.env.TEST_LESSON_ID || '69e12240-092f-4153-8896-d09b8a037858'

test.describe('lesson chat UI', () => {
  test('login and send chat in teach page', async ({ page, request, baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not set')

    // programmatic login via API and session cookie/localStorage
    const loginResp = await request.post(`${baseURL}/api/auth/login`, {
      data: {
        email: TEST_USER_EMAIL,
        password: TEST_USER_PASSWORD,
      },
    })
    expect(loginResp.ok()).toBeTruthy()
    const loginJson = await loginResp.json()
    expect(loginJson.session_id).toBeTruthy()

    await page.goto(`${baseURL}/`)
    await page.evaluate((sessionId) => {
      localStorage.setItem('session_id', sessionId)
    }, loginJson.session_id)

    // refresh to apply auth state
    await page.reload()

    // navigate to known published lesson
    await page.goto(`${baseURL}/teach/${LESSON_ID}`)

    // the page should show lesson title and input bar
    await expect(page.locator('nav', { hasText: 'Back' })).toBeVisible({ timeout: 20000 })
    const textarea = page.locator('textarea')
    await expect(textarea).toBeVisible({ timeout: 20000 })

    // type and send a chat turn
    await textarea.fill('Playwright test message')
    await textarea.press('Enter')

    // instruction: user text is submitted and cleared
    await expect(textarea).toHaveValue('')

    // verify turn appears in conversation view (as user bubble)
    const userBubble = page.locator('div[role=nothing]', { hasText: 'Playwright test message' })
    // fallback to text search in the conversation area
    await expect(page.locator('div', { hasText: 'Playwright test message' })).toBeVisible({ timeout: 10000 })
  })
})
