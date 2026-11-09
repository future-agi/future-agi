// HR-JRN-01: the child run harness/value-free-live.spec.ts spawns (never run on its own).
// Its own reporters, output and test match, so the main suite and the flow catalog
// never see the *.child.ts files; the parent names the file to run. E2E_VALUE_FREE=1
// adds lib/value-free-reporter.ts first, exactly as playwright.config.ts does;
// E2E_VALUE_FREE_CHILD_LIST_REPORTER=1 lists it whatever the switch (unset, the run
// must be refused). The json, html and blob reports are all scanned by the parent.
// The json report is an outputFile option, plus a second one whose file name carries an
// org key's prefix read from Postgres; or, with E2E_VALUE_FREE_CHILD_JSON=name, the one
// report PLAYWRIGHT_JSON_OUTPUT_NAME selects.
import { defineConfig, type ReporterDescription } from '@playwright/test';
import { E2E } from '../../lib/env';
import { VALUE_FREE_REPORTER, withValueFreeReporter } from '../../lib/value-free';

const out = process.env.E2E_VALUE_FREE_CHILD_OUT;
if (!out) throw new Error('E2E_VALUE_FREE_CHILD_OUT is set by harness/value-free-live.spec.ts');
const probes = JSON.parse(process.env.E2E_VALUE_FREE_PROBES ?? 'null') as { keyPrefix?: string } | null;
const json: ReporterDescription[] = process.env.E2E_VALUE_FREE_CHILD_JSON === 'name' ? [['json']]
  : [['json', { outputFile: `${out}/results.json` }],
    ...(probes?.keyPrefix ? [['json', { outputFile: `${out}/report-${probes.keyPrefix}.json` }] as ReporterDescription] : [])];
const reporters: ReporterDescription[] = [['line'], ...json,
  ['html', { outputFolder: `${out}/html`, open: 'never' }], ['blob', { outputDir: `${out}/blob` }]];

export default defineConfig({
  testDir: '.',
  testMatch: ['*.child.ts'],
  fullyParallel: true,
  // One worker per test: a token must agree across worker processes of one run.
  workers: 2,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 10_000 },
  outputDir: `${out}/artifacts`,
  reporter: process.env.E2E_VALUE_FREE_CHILD_LIST_REPORTER === '1' ? [[VALUE_FREE_REPORTER], ...reporters]
    : withValueFreeReporter(reporters),
  use: { baseURL: E2E.appUrl, trace: 'off', screenshot: 'off', video: 'off' },
});
