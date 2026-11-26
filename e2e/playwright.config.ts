import { defineConfig } from '@playwright/test';
import { E2E } from './lib/env';
import { grepInvertPattern } from './lib/quarantine';
import { SPEC_GLOBS, parseShard, shardTestMatch } from './lib/shard';

// E2E_SHARD=k/N (CI sets it per matrix leg) runs one duration-balanced share of
// the spec files; see lib/shard.ts. Each shard's blob report needs its own file
// name, because the report job merges all of them from one directory.
const shard = parseShard(process.env.E2E_SHARD);
const blobName = shard ? `report-${shard.index}-of-${shard.total}.zip` : 'report.zip';

// These specs need the entitled Error Feed stack (E2E_STACK=error-feed bin/e2e);
// other stacks leave them out of the listing instead of skipping them at run time.
const ERROR_FEED_STACK_ONLY = [
  'flows/error-feed/automatic-escalation-recovery.spec.ts',
  'flows/error-feed/creation-defaults.spec.ts',
  'flows/error-feed/escalation-presets.spec.ts',
  'flows/error-feed/escalation-settings-scope.spec.ts',
  'flows/error-feed/escalation-settings.spec.ts',
  'flows/error-feed/manual-status-protection.spec.ts',
  'flows/error-feed/membership-revisions.spec.ts',
  'flows/error-feed/settings-conflicts.spec.ts',
  'flows/error-feed/settings-reevaluation.spec.ts',
  'harness/error-feed-source.spec.ts',
];

export default defineConfig({
  testDir: '.',
  testMatch: shardTestMatch() ?? SPEC_GLOBS,
  testIgnore: process.env.E2E_STACK === 'error-feed' || process.env.E2E_INCLUDE_ERROR_FEED_STACK
    ? undefined : ERROR_FEED_STACK_ONLY,
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
    ? [['blob', { fileName: blobName }], ['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: E2E.appUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: process.env.E2E_RECORD_VIDEO === '1' ? 'on' : 'off',
  },
});
