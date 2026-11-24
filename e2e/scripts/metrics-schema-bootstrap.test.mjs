import test from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';

const root = fileURLToPath(new URL('../../', import.meta.url));
const schemaDir = path.join(root, 'futureagi/tracer/services/clickhouse/v2/schema');
const installer = path.join(root, 'futureagi/tracer/services/derived_metrics/schema_install.py');
const services = async (file) => YAML.parse(await readFile(path.join(root, file), 'utf8'), {
  customTags: ['!reset', '!override'].map(tag => ({ tag, collection: 'seq', resolve: value => value })),
  merge: true,
}).services;

// The native/CDC bootstraps create no Metrics table, so DASH-E2E-017's refresh
// can only publish if the root job applies every packaged Metrics schema file
// and this stack turns Metrics on for it.
test('the E2E stack applies the whole packaged Metrics ClickHouse schema before metric services start', async () => {
  const base = await services('docker-compose.distributed.yml');
  const job = base['metrics-schema-bootstrap'];
  assert.deepEqual(job.entrypoint, ['python', '-m', 'tracer.services.derived_metrics.schema_install']);
  assert.deepEqual(job.command, ['--apply', '--advisory'], 'no --force, no password in argv; never blocks startup');
  assert.equal(job.depends_on['clickhouse-cdc-bootstrap'].condition, 'service_completed_successfully');

  // The installer's one file list: 009, 011, then every packaged 028-038 file.
  const source = await readFile(installer, 'utf8');
  const block = source.slice(source.indexOf('FILES = ('), source.indexOf(')', source.indexOf('FILES = (')));
  const files = [...block.matchAll(/"(\d{3}_[a-z0-9_]+\.sql)"/g)].map(match => match[1]);
  const packaged = (await readdir(schemaDir)).filter(name => /^\d{3}_.*\.sql$/.test(name)).sort();
  // 028_reconcile_metadata_dictionaries.sql (from main, v1.59.0) shares the 028
  // prefix but repairs trace/user/session dictionaries; it is not a Metrics file.
  const metrics = packaged.filter(name => Number(name.slice(0, 3)) >= 28 && Number(name.slice(0, 3)) <= 38
    && name !== '028_reconcile_metadata_dictionaries.sql');
  assert.equal(metrics.length, 11);
  // 028 alters tracer_eval_logger_v2 (011), whose materialized view writes 009's rollup.
  assert.deepEqual(files, ['009_per_eval_rollup.sql', '011_eval_logger_v2.sql', ...metrics]);

  const all = await services('e2e/stack/docker-compose.e2e.yml');
  const e2e = all['metrics-schema-bootstrap'];
  assert.equal(e2e.environment.METRICS_ENABLED, '1');
  assert.ok(!('entrypoint' in e2e) && !('command' in e2e), 'the root job runs unchanged');
  assert.equal(e2e.environment.FUTURE_AGI_TELEMETRY_URL, 'http://127.0.0.1:9');
  assert.equal(e2e.environment.FUTURE_AGI_TELEMETRY_DISABLED, 'true');
  for (const name of ['backend', 'worker', 'worker-default']) {
    assert.equal(base[name].depends_on['metrics-schema-bootstrap'].condition, 'service_completed_successfully', name);
  }
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
