// HR-JRN-03 composition entrypoint. W00 executes once per browser project;
// multiple J00 consumers share its result, not its mutable ScopeActors.
// Explicit run ID and result directory are required, outside outputDir so
// Playwright project cleanup cannot erase a dependency's result.
import { defineConfig } from '@playwright/test';
import walk from './playwright.walk.config';
if (!process.env.E2E_CENSUS_RUN_ID || !process.env.E2E_CENSUS_RESULTS) {
  throw new Error('W00_RESULT_RUN_AND_DIRECTORY_REQUIRED');
}
export default defineConfig({ ...walk,
  projects: (walk.projects ?? []).flatMap(project => [
    { ...project, name: `w00-${project.name}`, testMatch: ['flows/walks/default-off-census.spec.ts'] },
    { ...project, name: project.name, dependencies: [`w00-${project.name}`],
      testMatch: ['flows/journeys/**/*.spec.ts', 'walks/census-consumer.spec.ts'] },
  ]),
});
