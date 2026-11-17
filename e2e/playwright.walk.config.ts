// H1: the walk runner (e2e/walks/README.md). The same suite rules as
// playwright.config.ts (retries 0, the one grepInvertPattern), with two browser
// projects, Chromium and Firefox, each running every walk test against its own
// ScopeActors org set (test-scoped: a fresh Owner A, Admin, Member, Viewer and
// Owner B per test and project).
//
// Workers: at most 3 per Standalone VM, whatever the core count. The recipe's
// "workers = cores" is for unit suites; the e2e README's measured
// limit wins here: 4 or more concurrent browsers overwhelm the single-Granian
// Standalone backend and the SPA never finishes booting. Parallelism comes
// from more VMs (one per walk group, e2e/walks/launch/), not more workers.
import { defineConfig } from '@playwright/test';
import { E2E } from './lib/env';
import { grepInvertPattern } from './lib/quarantine';
import { withValueFreeReporter, withValueFreeUse } from './lib/value-free';
import { chromiumStrictArgs, FIREFOX_STRICT_PREFS, strictBrowser } from './lib/walk/browsers';

export const WALK_WORKER_CAP = 3;
const requested = Number(process.env.E2E_WALK_WORKERS ?? WALK_WORKER_CAP);
if (!Number.isInteger(requested) || requested < 1 || requested > WALK_WORKER_CAP) {
  throw new Error(`E2E_WALK_WORKERS=${process.env.E2E_WALK_WORKERS} is not 1..${WALK_WORKER_CAP} (the Standalone browser cap)`);
}
const strict = strictBrowser();
const out = process.env.E2E_WALK_OUT ?? 'walk-results';

export default defineConfig({
  testDir: '.',
  testMatch: ['flows/walks/**/*.spec.ts'],
  // Trace scrub sweep + independent audit of the output directory.
  globalTeardown: './lib/trace-hygiene/global-teardown.ts',
  grepInvert: grepInvertPattern(),
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: requested,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  outputDir: `${out}/artifacts`,
  // The HTML report only on request (E2E_WALK_HTML=1): the launcher pulls ${out}, and the
  // receipts, results.json and screenshots are what a reviewer reads. E2E_VALUE_FREE=1
  // (HR-JRN-01) puts lib/value-free-reporter.ts first and turns trace, screenshot and video
  // off; the launcher then pulls only the publish directory. Unset, this exact list and use.
  reporter: withValueFreeReporter([['list'], ['json', { outputFile: `${out}/results.json` }],
    ...(process.env.E2E_WALK_HTML === '1' ? [['html', { open: 'never', outputFolder: `${out}-html` }] as const] : [])]),
  use: withValueFreeUse({
    baseURL: E2E.appUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    serviceWorkers: 'block',
    video: process.env.E2E_RECORD_VIDEO === '1' ? 'on' : 'off',
  }),
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', ...(strict ? { launchOptions: { args: chromiumStrictArgs() } } : {}) } },
    { name: 'firefox', use: { browserName: 'firefox',
      ...(strict ? { launchOptions: { firefoxUserPrefs: { ...FIREFOX_STRICT_PREFS } } } : {}) } },
  ],
});
