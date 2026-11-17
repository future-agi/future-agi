// Same specs, fixtures and expectations as the legacy suite; only the browser's
// external DNS/background services are disabled on the isolated cloud rehearsal.
// Unit gates use playwright.config.ts outside the kit; strict product walks use
// playwright.walk.config.ts. This does not alter either original configuration.
import { defineConfig } from '@playwright/test';
import legacy from './playwright.config';
import { chromiumStrictArgs } from './lib/walk/browsers';

export default defineConfig({ ...legacy,
  outputDir: process.env.E2E_WALK_OUT ?? legacy.outputDir,
  use: { ...legacy.use, browserName: 'chromium',
  launchOptions: { args: chromiumStrictArgs() } } });
