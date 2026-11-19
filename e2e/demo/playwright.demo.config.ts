import { defineConfig } from '@playwright/test';

// Demo recording only: one worker, no retries, long budget, video handled by the script.
export default defineConfig({
  testDir: '.',
  testMatch: ['*.demo.ts'],
  workers: 1,
  retries: 0,
  timeout: 1_800_000,
  expect: { timeout: 60_000 },
  reporter: [['list']],
  // The recording host serves the stack under a public HTTPS origin (demo/public-origin.mjs, local CA).
  use: { baseURL: process.env.E2E_APP_URL ?? 'http://localhost:3100', ignoreHTTPSErrors: true },
});
