// HR-JRN-01 (S3): the child run harness/value-free-structural.spec.ts spawns to show that a worker is
// admitted only through its own runner (never run on its own; no stack, no browser, no network: the child test's
// requests go to a stub). One test, value-free/admission.child.ts, one worker, under line and json.
// E2E_VALUE_FREE_ADMISSION_MODE: `first` lists lib/value-free-reporter.ts first (withValueFreeReporter, as every
// value-free config does), so the runner admits the publication; `late` lists it after line, so it admits nothing.
import { defineConfig, type ReporterDescription } from '@playwright/test';
import { VALUE_FREE_REPORTER, withValueFreeReporter } from '../../lib/value-free';

const out = process.env.E2E_VALUE_FREE_CHILD_OUT;
if (!out) throw new Error('E2E_VALUE_FREE_CHILD_OUT is not set: harness/value-free-structural.spec.ts spawns this config');
const line: ReporterDescription = ['line'];
const json: ReporterDescription = ['json', { outputFile: `${out}/results.json` }];

export default defineConfig({
  testDir: '.',
  testMatch: ['admission.child.ts'],
  workers: 1,
  retries: 0,
  timeout: 60_000,
  outputDir: `${out}/artifacts`,
  reporter: process.env.E2E_VALUE_FREE_ADMISSION_MODE === 'late' ? [line, [VALUE_FREE_REPORTER], json]
    : withValueFreeReporter([line, json]),
  use: { trace: 'off', screenshot: 'off', video: 'off' },
});
