import { defineConfig } from '@playwright/test';
import { E2E } from './lib/env';
import { grepInvertPattern } from './lib/quarantine';
import { withValueFreeReporter, withValueFreeUse } from './lib/value-free';

export default defineConfig({
  testDir: '.',
  testMatch: ['flows/**/*.spec.ts', 'harness/**/*.spec.ts'],
  // 708 add.78: scrub every trace archive left in the output directory, audit it
  // with the independent detector and fail the run on any finding (lib/trace-hygiene/).
  globalTeardown: './lib/trace-hygiene/global-teardown.ts',
  grepInvert: grepInvertPattern(),
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // 4+ concurrent browsers overwhelm the stack's single-Granian-worker backend and the app never finishes booting; CI already runs 2.
  workers: process.env.CI ? 2 : 3,
  // Covers ordinary flows; a flow waiting on a longer POLL budget (e.g. CDC_VISIBLE, 180s) raises its own limit via test.setTimeout.
  timeout: 120_000,
  expect: { timeout: 10_000 },
  // E2E_VALUE_FREE=1 puts lib/value-free-reporter.ts first (HR-JRN-01); unset, this exact list.
  reporter: withValueFreeReporter(process.env.CI
    ? [['blob'], ['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]]),
  // E2E_VALUE_FREE=1 turns trace, screenshot and video off (HR-JRN-01); unset, these exact options.
  use: withValueFreeUse({
    baseURL: E2E.appUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: process.env.E2E_RECORD_VIDEO === '1' ? 'on' : 'off',
  }),
});
