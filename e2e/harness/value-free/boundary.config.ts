// HR-JRN-01 round 4: the child run harness/value-free-publish.spec.ts spawns for each
// probe of the publication boundary (never run on its own; no stack, no browser). One
// test, value-free/boundary.child.ts, under the reporters a journey run lists: line,
// json, html and blob. E2E_VALUE_FREE_BOUNDARY names the probe: `source-ext` names the
// json report `report-<probe>.ts`; `late-reporter` adds value-free/late-reporter.cjs
// after them. Everything else a probe needs comes from its environment.
import { fileURLToPath } from 'node:url';
import { defineConfig, type ReporterDescription } from '@playwright/test';
import { withValueFreeReporter } from '../../lib/value-free';

const out = process.env.E2E_VALUE_FREE_CHILD_OUT;
if (!out) throw new Error('E2E_VALUE_FREE_CHILD_OUT is set by harness/value-free-publish.spec.ts');
const mode = process.env.E2E_VALUE_FREE_BOUNDARY ?? 'control';
const probes = JSON.parse(process.env.E2E_VALUE_FREE_PROBES ?? '{}') as { label?: string };
const reporters: ReporterDescription[] = [['line'],
  ['json', { outputFile: mode === 'source-ext' ? `${out}/report-${probes.label}.ts` : `${out}/results.json` }],
  ['html', { outputFolder: `${out}/html`, open: 'never' }], ['blob', { outputDir: `${out}/blob` }],
  ...(mode === 'late-reporter' ? [[fileURLToPath(new URL('./late-reporter.cjs', import.meta.url))] as ReporterDescription] : [])];

export default defineConfig({
  testDir: '.',
  testMatch: ['boundary.child.ts'],
  workers: 1,
  retries: 0,
  timeout: 60_000,
  outputDir: `${out}/artifacts`,
  reporter: withValueFreeReporter(reporters),
  use: { trace: 'off', screenshot: 'off', video: 'off' },
});
