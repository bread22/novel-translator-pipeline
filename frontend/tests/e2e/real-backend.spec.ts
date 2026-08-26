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
