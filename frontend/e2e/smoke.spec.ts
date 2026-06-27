import { test, expect } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://118.31.57.25/agent-ops'
const USER = `e2e_${Date.now()}`
const PASS = 'testpass123'

test.describe('Agent Ops E2E', () => {
  test('register, login, browse market', async ({ page }) => {
    await page.goto(`${BASE}/register`)
    await page.locator('#reg-username').fill(USER)
    await page.locator('#reg-display').fill('E2E User')
    await page.locator('#reg-password').fill(PASS)
    await page.locator('#reg-password2').fill(PASS)
    await page.getByRole('button', { name: '注册并登录' }).click()
    await expect(page).toHaveURL(/\/agent-ops\/?$/)
    await expect(page.getByRole('heading', { name: '总览' })).toBeVisible()

    await page.getByRole('link', { name: '账号市场' }).click()
    await expect(page.getByText('购买账号').first()).toBeVisible({ timeout: 15000 })

    await page.getByRole('link', { name: '个人资料' }).click()
    await expect(page.getByText(USER)).toBeVisible()
  })

  test('login with existing flow', async ({ page }) => {
    const user = `e2e_login_${Date.now()}`
    await page.goto(`${BASE}/register`)
    await page.locator('#reg-username').fill(user)
    await page.locator('#reg-display').fill('E2E Login')
    await page.locator('#reg-password').fill(PASS)
    await page.locator('#reg-password2').fill(PASS)
    await page.getByRole('button', { name: '注册并登录' }).click()
    await expect(page.getByRole('heading', { name: '总览' })).toBeVisible()

    await page.getByRole('button', { name: '退出登录' }).click()
    await page.locator('#login-username').fill(user)
    await page.locator('#login-password').fill(PASS)
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page.getByRole('heading', { name: '总览' })).toBeVisible()
  })
})
