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
  assert.deepEqual(job.command, ['--apply'], 'no --force, no password in argv');
  assert.equal(job.depends_on['clickhouse-cdc-bootstrap'].condition, 'service_completed_successfully');

  // The installer's one file list: 009, 011, then every packaged 028-038 file.
  const source = await readFile(installer, 'utf8');
  const block = source.slice(source.indexOf('FILES = ('), source.indexOf(')', source.indexOf('FILES = (')));
  const files = [...block.matchAll(/"(\d{3}_[a-z0-9_]+\.sql)"/g)].map(match => match[1]);
  const packaged = (await readdir(schemaDir)).filter(name => /^\d{3}_.*\.sql$/.test(name)).sort();
  const metrics = packaged.filter(name => Number(name.slice(0, 3)) >= 28 && Number(name.slice(0, 3)) <= 38);
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
