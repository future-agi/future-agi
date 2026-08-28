import { defineConfig } from '@playwright/test';
import { E2E } from './lib/env';
import { grepInvertPattern } from './lib/quarantine';

export default defineConfig({
  testDir: '.',
  testMatch: ['flows/**/*.spec.ts', 'harness/**/*.spec.ts'],
  grepInvert: grepInvertPattern(),
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // 4+ concurrent browsers overwhelm the stack's single-Granian-worker backend and the app never finishes booting; CI already runs 2.
  workers: process.env.CI ? 2 : 1,
  // Covers ordinary flows; a flow waiting on a longer POLL budget (e.g. CDC_VISIBLE, 180s) raises its own limit via test.setTimeout.
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [['blob'], ['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: E2E.appUrl,
    // `retain-on-failure` is not cheaper than `on` while a test runs: it
    // buffers every action and DOM snapshot for EVERY test and only discards
    // on pass. On a 16-18GB laptop also running the Docker stack, that spike
    // tips macOS into jetsam, which SIGKILLs the largest process — the editor
    // ("window terminated unexpectedly, reason: 'killed', code: '9'").
    // CI has the headroom and needs the artifact; locally it is opt-in via
    // `E2E_TRACE=1`. Screenshots stay on: captured once, at failure.
    trace: process.env.CI || process.env.E2E_TRACE ? 'retain-on-failure' : 'off',
    screenshot: 'only-on-failure',
    video: 'off',
  },
});
