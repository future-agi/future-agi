import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { E2E } from './lib/env';

// Required, separate cloud qualification. No skip/retry/fallback to the ordinary runner.
// Per-flow timeout arithmetic belongs in the separately approved flow, not this config.
if (!process.env.CTXHUB_NATIVE_ATTESTATION) {
  throw new Error('Native runner unavailable: provide a real-ref, package-pinned CTXHUB_NATIVE_ATTESTATION.');
}

export default defineConfig({
  testDir: '.',
  testMatch: ['flows/skills/**/*.native.spec.ts'],
  globalSetup: fileURLToPath(new URL('./harness/skills-r3/native-setup.mjs', import.meta.url)),
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: E2E.appUrl,
    trace: 'on',
    screenshot: 'only-on-failure',
    video: 'on',
  },
});
