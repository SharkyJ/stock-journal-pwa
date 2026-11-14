import { test, expect } from '@playwright/test';

test('독립 매도법 탭 설정과 이력 저장', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await page.locator('[data-act="demoOn"]').click();
  await page.locator('[data-act="tab"][data-val="strat"]').click();
  await expect(page.locator('[data-k="sg"]')).toHaveText(['매도법', '신호', '기록', '도구']);
  await page.locator('[data-act="sellForm"]').first().click();
  await page.locator('[name="profit"]').selectOption('1');
  await page.locator('[name="ma"]').selectOption('1');
  await page.locator('[data-act="sellSave"]').click();
  await expect(page.locator('#modal')).not.toBeVisible();
  await expect(page.locator('#sgv')).toContainText('설정 수정');
  await expect(page.locator('#sgv')).toContainText('규칙 설정 변경');
  await expect(page.locator('#sgv')).toContainText('2/3 수익보호');
  expect(errors).toEqual([]);
});
