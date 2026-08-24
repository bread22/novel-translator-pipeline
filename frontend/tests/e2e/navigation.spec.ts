import { expect, test } from '@playwright/test';

test('five primary views remain addressable by URL', async ({ page }) => {
  for (const tab of ['queue', 'studio', 'reader', 'knowledge', 'settings']) {
    await page.goto(`/#/${tab}`);
    await expect(page.locator('main')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`#/${tab}$`));
  }
});
