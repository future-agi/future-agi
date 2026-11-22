import test from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';

const root = fileURLToPath(new URL('../../', import.meta.url));
const schemaDir = path.join(root, 'futureagi/tracer/services/clickhouse/v2/schema');
const services = async () => YAML.parse(await readFile(path.join(root, 'e2e/stack/docker-compose.e2e.yml'), 'utf8'), {
  customTags: ['!reset', '!override'].map(tag => ({ tag, collection: 'seq', resolve: value => value })),
}).services;

// The Distributed bootstraps create no Metrics table, so DASH-E2E-017's refresh
// can only publish if this job applies every packaged Metrics schema file.
test('the E2E stack applies the whole packaged Metrics ClickHouse schema before metric services start', async () => {
  const all = await services();
  const job = all['metrics-schema-bootstrap'];
  assert.deepEqual(job.entrypoint, ['python', '-m', 'tracer.services.clickhouse.v2.apply_schema']);
  assert.ok(job.command.includes('--ch-host=clickhouse'));
  assert.ok(job.command.includes('--ch-http-port=8123'));
  assert.ok(!job.command.some(arg => arg.startsWith('--ch-password')), 'the password stays out of argv');
  assert.ok(!job.command.includes('--force'));
  const files = job.command.slice(job.command.indexOf('--files') + 1);
  const packaged = (await readdir(schemaDir)).filter(name => /^\d{3}_.*\.sql$/.test(name)).sort();
  const metrics = packaged.filter(name => Number(name.slice(0, 3)) >= 28 && Number(name.slice(0, 3)) <= 38);
  assert.equal(metrics.length, 11);
  // 028 alters tracer_eval_logger_v2 (011), whose materialized view writes 009's rollup.
  assert.deepEqual(files, ['009_per_eval_rollup.sql', '011_eval_logger_v2.sql', ...metrics]);
  assert.equal(job.depends_on['clickhouse-cdc-bootstrap'].condition, 'service_completed_successfully');
  assert.equal(job.environment.FUTURE_AGI_TELEMETRY_URL, 'http://127.0.0.1:9');
  assert.equal(job.environment.FUTURE_AGI_TELEMETRY_DISABLED, 'true');
  for (const name of ['backend', 'worker', 'worker-tasks-s', 'worker-tasks-l', 'worker-exact-aggregation']) {
    assert.equal(all[name].depends_on['metrics-schema-bootstrap'].condition, 'service_completed_successfully', name);
  }
});

// Standalone has no compose job for it: bin/e2e runs the same runner over the
// same files in the app container once the app's bootstrap has finished.
test('Standalone applies the same Metrics schema files as the Distributed job', async () => {
  const job = (await services())['metrics-schema-bootstrap'];
  const distributed = job.command.slice(job.command.indexOf('--files') + 1);
  const script = await readFile(path.join(root, 'bin/e2e'), 'utf8');
  const list = script.match(/^METRICS_SCHEMA_FILES=\(([^)]*)\)$/m);
  assert.ok(list, 'bin/e2e defines METRICS_SCHEMA_FILES');
  assert.deepEqual(list[1].trim().split(/\s+/), distributed);
  const standalone = script.slice(script.indexOf('if [[ "${E2E_STACK:-distributed}" == standalone ]]; then'));
  const apply = standalone.slice(standalone.indexOf('Applying the Metrics ClickHouse schema'), standalone.indexOf('phase metrics-schema'));
  assert.match(apply, /exec -T -w \/app\/backend .* app \\\n/s);
  assert.match(apply, /python -m tracer\.services\.clickhouse\.v2\.apply_schema --ch-host="\$CH_HOST"/);
  assert.match(apply, /--files "\$@"' \\\n\s+metrics-schema "\$\{METRICS_SCHEMA_FILES\[@\]\}"/);
  assert.ok(!/--ch-password|--force/.test(apply), 'no password in argv, no forced replay');
  // Before the frontend wait, so it precedes every test in this run.
  assert.ok(standalone.indexOf('phase metrics-schema') < standalone.indexOf('Waiting for frontend'));
});
