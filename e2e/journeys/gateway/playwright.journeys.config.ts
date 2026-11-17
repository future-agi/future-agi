import { defineConfig } from '@playwright/test';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { readBinding, titleGrep } = require('./_invocation/consumer.cjs');
const { catalogue } = require('./_invocation/catalogue.cjs');
const binding = readBinding();
const item = binding.mode === 'execute'
  ? catalogue.find((row: { id: string }) => row.id === binding.expectedIds[0]) : undefined;

// Separate additive config, NOT the harness/W20 config. Source defaults for
// timeout/expect/retries: da4c81cc62:e2e/playwright.config.ts. The runtime owns
// browser archives, policies, guards, app-side locality and context observation.
// No launchOptions, Chromium flags, Firefox prefs, remote or persistent context.
// CLI argv is verified in the launcher reporter/globalSetup, not here: workers
// reload this config with their own worker-process argv, not the launcher's.
if (item?.kind === 'product-fragment' && (process.env.E2E_STACK !== binding.stack ||
    process.env.E2E_GATEWAY_PROFILE !== binding.profile ||
    process.env.E2E_APP_URL !== binding.appUrl || process.env.E2E_GATEWAY_URL !== binding.gatewayUrl)) {
  throw new Error('F01 anonymous stock fixture environment differs from admitted binding');
}

export default defineConfig({
  testDir: '.',
  // Deliberately broad discovery inside this directory: a new/extra spec must be
  // discovered and REFUSED by the exact inventory, not silently dropped by a list.
  testMatch: item ? [`**/${item.file.slice('journeys/gateway/'.length)}`] : ['**/*.spec.ts'],
  ...(item ? { grep: new RegExp(titleGrep(item.title)) } : {}),
  testIgnore: ['**/_invocation/**'],
  globalSetup: require.resolve('./_invocation/global-setup.cjs'),
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  repeatEach: 1,
  workers: 1,
  maxFailures: 0,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  outputDir: path.join(binding.outputRoot, 'playwright'),
  // CLI is authoritative and the compiler may append accepted base reporters,
  // but cannot replace either of these. No HTML/cloud reporter or auto-open.
  reporter: [['line'], [require.resolve('./_invocation/reporter.cjs')]],
  // Browser comes from the same create-only binding in launcher AND workers,
  // not CLI --browser (which may synthesize project names). No --project filter.
  // Both browser invocations are mandatory and independently reconciled.
  projects: [{ name: '' }],
  use: {
    browserName: binding.browser,
    ...(binding.viewport ? { viewport: binding.viewport } : {}),
    ...(item?.kind === 'product-fragment' ? { baseURL: binding.appUrl } : {}),
    // Current packet sources forbid raw headers/tokens/unmasked captures. Native
    // stdout/stderr and allowed testInfo.attach originals stay with the runner.
    trace: 'off', screenshot: 'off', video: 'off',
  },
});
