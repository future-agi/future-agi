import { defineConfig } from '@playwright/test';
import { E2E } from './lib/env';
import { grepInvertPattern } from './lib/quarantine';

// Flows that need a fresh Community install of their own: they run only on
// the Community lane (E2E_STACK=community bin/e2e test, README "The Community
// edition lane"); every other spec runs on the shared, licensed stacks.
const COMMUNITY_LANE_SPECS = ['flows/settings/community-edition.spec.ts'];

export default defineConfig({
  testDir: '.',
  projects: [
    { name: 'default', testMatch: ['flows/**/*.spec.ts', 'harness/**/*.spec.ts'],
      testIgnore: COMMUNITY_LANE_SPECS },
    { name: 'community-edition', testMatch: COMMUNITY_LANE_SPECS },
  ],
  grepInvert: grepInvertPattern(),
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // 4+ concurrent browsers overwhelm the stack's single-Granian-worker backend and the app never finishes booting; CI already runs 2.
  workers: process.env.CI ? 2 : 3,
  // Covers ordinary flows; a flow waiting on a longer POLL budget (e.g. CDC_VISIBLE, 180s) raises its own limit via test.setTimeout.
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [['blob'], ['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: E2E.appUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
});
