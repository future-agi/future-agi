// AUTH-E2E-004 uses the existing walk browser projects and worker cap, without
// modifying the shared harness. Run only on an isolated cloud-managed stack.
import { defineConfig } from '@playwright/test';
import walk from './playwright.walk.config';

if (process.env.E2E_WALK_STRICT_BROWSER === '0') throw new Error('AUTH004_STRICT_BROWSER_REQUIRED');
const out = process.env.E2E_LANDING_OUT ?? 'landing-results';

export default defineConfig({
  ...walk,
  testMatch: ['flows/auth/workspace-landing.spec.ts'],
  workers: 1,
  outputDir: `${out}/artifacts`,
  // The existing count-only reporter never publishes errors, tokens or bodies.
  // The spec separately persists a value-free JSON receipt for every case.
  reporter: [['./walks/vm/counts-reporter.cjs', { outputFile: `${out}/counts.json` }]],
  use: { ...walk.use, trace: 'off', screenshot: 'off', video: 'off', serviceWorkers: 'block' },
});
