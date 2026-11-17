// Consume the accepted census graph unchanged: W00 runs once per browser and
// publishes before that browser's journey consumers. Never use --no-deps.
import { defineConfig } from '@playwright/test';
import census from './playwright.census.config';
import { chromiumStrictArgs, FIREFOX_STRICT_PREFS } from './lib/walk/browsers';
import { requireJourneyPublication } from './flows/journeys/publication-contract';

// A direct CLI invocation must refuse before W00 or ScopeActors provisions.
// Setting E2E_VALUE_FREE alone is not proof of an accepted publication seam.
requireJourneyPublication();

export default defineConfig({
  ...census,
  workers: 1,
  retries: 0,
  reporter: [['./lib/value-free-reporter.ts'], ['json']],
  use: { ...census.use, trace: 'off', screenshot: 'off', video: 'off', serviceWorkers: 'block' },
  projects: (census.projects ?? []).map(project => ({
    ...project,
    use: { ...project.use,
      launchOptions: project.use?.browserName === 'firefox'
        ? { firefoxUserPrefs: { ...FIREFOX_STRICT_PREFS } }
        : { args: chromiumStrictArgs() },
    },
  })),
});
