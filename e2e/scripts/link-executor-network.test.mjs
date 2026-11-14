import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';

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
  assert.notEqual(config.services['link-canary'].networks['link-public'].ipv4_address,
    config.services['link-worker-canary'].networks['link-public'].ipv4_address,
    'parallel browser and Temporal tests must have independent listener journals');
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


test('standalone supplies the same listeners and source-selected internal executor', () => {
  const result = spawnSync('docker', ['compose', '--env-file', 'e2e/stack/standalone-e2e.env',
    '-f', 'docker-compose.yml', '-f', 'e2e/stack/docker-compose.standalone-e2e.yml',
    '--profile', '*', 'config', '--format', 'json'], { cwd: root, encoding: 'utf8', env: {
      ...process.env, FUTURE_AGI_TELEMETRY_URL: 'http://127.0.0.1:9', CODE_EXECUTOR_VERSION: 'source-witness',
    } });
  assert.equal(result.status, 0, result.stderr);
  const config = JSON.parse(result.stdout);
  const executor = config.services['code-executor'];
  assert.equal(executor.image, 'futureagi/code-executor:source-witness');
  assert.deepEqual(executor.profiles ?? [], [], 'ordinary down must remove the required executor');
  assert.deepEqual(Object.keys(executor.networks).sort(), ['link-private', 'link-public']);
  for (const name of Object.keys(executor.networks)) assert.equal(config.networks[name].internal, true);
  assert.ok(executor.networks['link-public'].aliases.includes('code-executor-nsjail'));
  assert.deepEqual(Object.keys(config.services.app.networks).sort(), ['default', 'link-public']);
  assert.equal(config.services.app.environment.CODE_EXECUTOR_URL, 'http://code-executor-nsjail:8060');
  assert.equal(config.services.app.environment.CODE_EXECUTOR_LOCAL_FALLBACK, 'false');
  assert.equal(config.services.app.environment.FUTURE_AGI_TELEMETRY_URL, 'http://127.0.0.1:9');
  for (const service of [executor, config.services.app]) {
    assert.equal(service.environment.LINK_EVAL_ALLOWED_HOSTS,
      'allowed.example,worker-allowed.example,private.example');
  }
  for (const [service, ip, port] of [['link-canary', '11.23.0.10', '28082'],
    ['link-worker-canary', '11.23.0.11', '28083']]) {
    assert.equal(config.services[service].networks['link-public'].ipv4_address, ip);
    assert.ok(config.services[service].ports.some(p => p.published === port && p.host_ip === '127.0.0.1'));
  }
  const hosts = Array.isArray(executor.extra_hosts)
    ? Object.fromEntries(executor.extra_hosts.map(value => value.split('='))) : executor.extra_hosts;
  assert.deepEqual(hosts, { 'allowed.example': '11.23.0.10', 'denied.example': '11.23.0.10',
    'worker-allowed.example': '11.23.0.11', 'private.example': '172.30.91.10' });
  const runner = readFileSync(new URL('../../bin/e2e', import.meta.url), 'utf8');
  assert.match(runner, /SERVICES=\(app postgres clickhouse mock-llm mock-google code-executor link-canary link-worker-canary\)/);
});

test('both CI legs build and select the executor from the checked-out source', () => {
  const workflow = parse(readFileSync(new URL('../../.github/workflows/e2e-ci.yml', import.meta.url), 'utf8'));
  const steps = workflow.jobs.e2e.steps;
  const build = steps.find(step => step.name === 'Build code-executor image from PR code');
  assert.equal(build.if, undefined, 'standalone must build too');
  assert.equal(build.with.context, 'futureagi/code-executor');
  assert.equal(build.with.file, 'futureagi/code-executor/Dockerfile');
  assert.equal(build.with.load, true);
  assert.equal(build.with.tags, 'futureagi/code-executor:${{ env.IMAGE_TAG }}');
  const selection = steps.find(step => step.name === 'Select the source-built executor in both stacks');
  assert.equal(selection.if, undefined);
  assert.equal(selection.run, 'echo "CODE_EXECUTOR_VERSION=$IMAGE_TAG" >> "$GITHUB_ENV"');
  assert.ok(steps.indexOf(build) < steps.indexOf(selection));
  assert.ok(steps.indexOf(selection) < steps.findIndex(step => step.name === 'Boot the e2e stack'));
});
