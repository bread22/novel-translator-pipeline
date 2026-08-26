import { defineConfig } from '@playwright/test';

const externalBaseURL = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './tests/e2e',
  use: { baseURL: externalBaseURL || 'http://127.0.0.1:4173' },
  ...(externalBaseURL ? {} : {
    webServer: {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: !process.env.CI,
    },
  }),
});
