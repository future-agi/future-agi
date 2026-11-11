// Cloud-only smoke of the installed application wheel, not an E2B/Simulation pass.
// CTXHUB_CATALOG must be libraryFixture({ unsupportedMember: false }): all 15 text members.
// The default binary-containing intake fixture is exercised separately by pytest.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { ProgrammableProvider, completion } from '../../lib/skills-r3/programmable-provider.mjs';

if (process.platform !== 'linux') throw new Error('Run only on the assigned cloud VM');
if (!process.env.CTXHUB_APP_PYTHON || !process.env.CTXHUB_SMOKE_OUTPUT || !process.env.CTXHUB_CATALOG) {
  throw new Error('Provide the installed wheel Python, catalog and a fresh output directory');
}
const tool = (id, name, args) => ({ id, type: 'function', function: { name, arguments: JSON.stringify(args) } });
const provider = await new ProgrammableProvider().listen();
try {
  const scenario = provider.register([
    { kind: 'json', body: completion({ content: null, tool_calls: [tool('select-refund', 'load_skill', { skill_id: '00000000-0000-4000-8000-000000000001' })] }, { finishReason: 'tool_calls' }) },
    { kind: 'json', body: completion({ content: null, tool_calls: [tool('read-policy', 'read_skill_file', { skill_id: '00000000-0000-4000-8000-000000000001', path: 'references/policy.md' })] }, { finishReason: 'tool_calls' }) },
    { kind: 'json', body: completion({ content: null, tool_calls: [tool('refund-operation', 'issue_refund', { amount: 25 })] }, { finishReason: 'tool_calls' }) },
    { kind: 'json', body: completion({ content: 'The synthetic refund of 25 units was applied.' }) },
  ]);
  const host = fileURLToPath(new URL('../../fixtures/skills-r3/support-application/tests/smoke_host.py', import.meta.url));
  const status = await new Promise((resolve, reject) => {
    const child = spawn(process.env.CTXHUB_APP_PYTHON, [host], {
      env: { ...process.env, CTXHUB_PROVIDER_URL: scenario.baseURL() }, stdio: 'inherit', timeout: 90_000,
    });
    child.once('error', reject);
    child.once('exit', (code, signal) => signal ? reject(new Error(`Smoke terminated: ${signal}`)) : resolve(code));
  });
  assert.equal(status, 0, 'Installed application smoke must succeed');
  const ledger = provider.ledger(scenario.capability);
  assert.equal(ledger.length, 4);
  assert.ok(ledger.every(row => row.state === 'responded'));
  assert.equal(provider.remaining(scenario.capability), 0);
  const receipt = JSON.parse(await readFile(`${process.env.CTXHUB_SMOKE_OUTPUT}/smoke-receipt.json`, 'utf8'));
  const requests = receipt.application.events.filter(event => event.kind === 'model_request');
  assert.deepEqual(requests.map(event => event.body.request_digest), ledger.map(row => `sha256:${row.request_sha256}`));
  const catalog = receipt.application.events.find(event => event.kind === 'catalog').body.members;
  assert.equal(catalog.length, 15, 'All native text catalog members must reach the application');
  await writeFile(`${process.env.CTXHUB_SMOKE_OUTPUT}/provider-ledger.json`, JSON.stringify(ledger, null, 2) + '\n');
} finally {
  await provider.close();
}
