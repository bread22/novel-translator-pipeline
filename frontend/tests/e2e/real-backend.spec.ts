import { expect, test } from '@playwright/test';

test.skip(
  !process.env.E2E_REAL,
  'Set E2E_REAL=1 with E2E_BASE_URL to run against a real frontend/backend service.',
);

test('real service exposes health and the queue shell', async ({ page, request }) => {
  const health = await request.get(process.env.E2E_HEALTH_URL || '/health');
  expect(health.ok()).toBeTruthy();
  await expect(health.json()).resolves.toMatchObject({ status: 'ok' });

  await page.goto('/#/queue');
  await expect(page.getByText('日文小说批量翻译与队列调度')).toBeVisible();
});

test('browser can reach the protected API when WEB_AUTH_TOKEN is enabled', async ({ page }) => {
  const token = process.env.E2E_AUTH_TOKEN;
  test.skip(!process.env.E2E_AUTH || !token, 'Set E2E_AUTH=1 and E2E_AUTH_TOKEN with WEB_AUTH_TOKEN to exercise the protected browser path.');

  const queueResponse = page.waitForResponse(
    response => response.url().includes('/api/v1/queue') && response.request().method() === 'GET',
  );
  await page.goto(`/?access_token=${encodeURIComponent(token)}#/queue`);
  expect((await queueResponse).status()).toBe(200);
});
