import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));

// Inspect Compose's merged graph, not just one file: YAML anchors in the base
// are expanded before the E2E overlay is applied to individual services.
test('every code-evaluation worker can reach the isolated executor without local fallback', () => {
  const result = spawnSync('docker', ['compose', '--env-file', 'e2e/stack/e2e.env',
    '-f', 'docker-compose.distributed.yml', '-f', 'e2e/stack/docker-compose.e2e.yml',
    '--profile', '*', 'config', '--format', 'json'], {
    cwd: root, encoding: 'utf8', env: { ...process.env,
      FUTURE_AGI_TELEMETRY_URL: 'http://127.0.0.1:9',
      CODE_EXECUTOR_LOCAL_FALLBACK: 'false',
    },
  });
  assert.equal(result.status, 0, result.stderr);
  const config = JSON.parse(result.stdout);
  const executorNetworks = Object.keys(config.services['code-executor'].networks);
  assert.deepEqual(executorNetworks.sort(), ['link-private', 'link-public']);
  for (const name of executorNetworks) assert.equal(config.networks[name].internal, true, name);
  const workers = Object.entries(config.services).filter(([, service]) =>
    service.environment?.SERVICE_TYPE === 'temporal-worker');
  assert.ok(workers.some(([name]) => name === 'worker'));
  assert.ok(workers.some(([name]) => name === 'worker-tasks-s'));
  for (const [name, service] of [['backend', config.services.backend], ...workers]) {
    assert.ok(Object.keys(service.networks).some(network => executorNetworks.includes(network)),
      `${name} must share an internal network with the executor`);
    assert.equal(service.environment.CODE_EXECUTOR_URL, 'http://code-executor:8060', name);
    assert.equal(service.environment.CODE_EXECUTOR_LOCAL_FALLBACK, 'false', name);
    assert.equal(service.environment.FUTURE_AGI_TELEMETRY_URL, 'http://127.0.0.1:9', name);
  }
});
