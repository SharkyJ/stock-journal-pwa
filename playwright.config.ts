import { defineConfig, devices } from '@playwright/test';

const PORT = 8874;
const baseURL = `http://localhost:${PORT}`;

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  /* 앱은 빌드 없는 정적 파일이므로 레포 루트를 그대로 서빙한다. */
  webServer: {
    command: `python3 -m http.server ${PORT}`,
    url: `${baseURL}/index.html`,
    reuseExistingServer: false,
  },
});
