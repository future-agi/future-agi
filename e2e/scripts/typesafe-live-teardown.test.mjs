import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import test from 'node:test';
import ts from 'typescript';
const libModule = new URL('../lib/typesafe-live.ts', import.meta.url).href;
registerHooks({ load(url, context, nextLoad) {
  const loaded = nextLoad(url, context);
  return url !== libModule ? loaded : { ...loaded, format: 'module', source: ts.transpileModule(String(loaded.source), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText };
} });
const lib = await import(libModule);

function fixture() {
  const existingProvider = { id: 'p-old', provider_name: 'unrelated' };
  const existingKey = { id: 'k-old', name: 'pre-existing', status: 'active', gateway_key_id: 'g-old' };
  const resources = lib.createTypeSafeResources([existingProvider], [existingKey]);
  resources.providerNames.add('typesafe');
  resources.providerNames.add('typesafe-badkey');
  resources.keyNames.add('run-main');
  let providers = [existingProvider, { id: 'p1', provider_name: 'typesafe' }, { id: 'p2', provider_name: 'typesafe-badkey' }];
  const keys = [existingKey, { id: 'k1', name: 'run-main', gateway_key_id: 'g1', status: 'active' }];
  const removed = [], revoked = [];
  const io = {
    listProviders: async () => providers,
    listKeys: async () => keys,
    deleteProvider: async id => { removed.push(id); providers = providers.filter(p => p.id !== id); },
    revokeKey: async id => { revoked.push(id); keys.find(k => k.id === id).status = 'revoked'; },
    gatewayProvidersAbsent: async names => names.every(name => !providers.some(p => p.provider_name === name)),
    gatewayKeyRevoked: async key => key.status === 'revoked',
    waitUntil: async check => { if (!await check()) throw new Error('timeout: sensitive-response-body'); },
  };
  return { resources, io, removed, revoked, keys };
}

test('teardown deletes both run providers, revokes only run-created keys and verifies both stores', async () => {
  const f = fixture();
  const result = await lib.teardownTypeSafe(f.resources, f.io);
  assert.equal(result.verdict, 'PASS');
  assert.deepEqual(f.removed, ['p1', 'p2']);
  assert.deepEqual(f.revoked, ['k1']);
  assert.equal(f.keys[0].status, 'active');
  assert.ok(result.checks.some(c => c.label === 'gateway provider config removal' && c.verdict === 'PASS'));
  assert.ok(result.checks.some(c => c.label === 'gateway key revocation' && c.verdict === 'PASS'));
});

test('response loss still discovers exact intended names; similar and baseline names are untouched', async () => {
  const f = fixture();
  f.resources.keyNames.add('pre-existing'); // Even an accidental name collision cannot revoke a baseline ID.
  f.keys.push({ id: 'k-other', name: 'run-main-other', status: 'active' });
  const result = await lib.teardownTypeSafe(f.resources, f.io);
  assert.equal(result.verdict, 'PASS');
  assert.deepEqual(f.revoked, ['k1']);
  assert.equal(f.keys[2].status, 'active');
});

test('one failed provider deletion does not skip other providers or keys; errors are data-free', async () => {
  const f = fixture(), remove = f.io.deleteProvider;
  f.io.deleteProvider = async id => { if (id === 'p1') throw new Error('secret-unregistered-provider-value'); await remove(id); };
  const result = await lib.teardownTypeSafe(f.resources, f.io);
  assert.equal(result.verdict, 'FAIL');
  assert.deepEqual(f.removed, ['p2']);
  assert.deepEqual(f.revoked, ['k1']);
  assert.doesNotMatch(JSON.stringify(result), /secret-unregistered|sensitive-response/);
});

test('provider inventory failure does not prevent key cleanup', async () => {
  const f = fixture();
  f.io.listProviders = async () => { throw new Error('inventory failed'); };
  const result = await lib.teardownTypeSafe(f.resources, f.io);
  assert.equal(result.verdict, 'FAIL');
  assert.deepEqual(f.revoked, ['k1']);
});

test('accepted deletion is insufficient when the gateway still holds the key', async () => {
  const f = fixture();
  f.io.gatewayProvidersAbsent = async () => false;
  assert.equal((await lib.teardownTypeSafe(f.resources, f.io)).verdict, 'FAIL');
});

test('local key revocation is insufficient when the gateway still accepts it', async () => {
  const f = fixture();
  f.io.gatewayKeyRevoked = async () => false;
  assert.equal((await lib.teardownTypeSafe(f.resources, f.io)).verdict, 'FAIL');
});

test('gateway absence rejects malformed responses and any remaining provider, even a changed mask', () => {
  const configs = providers => ({ data: { org: { providers } } });
  assert.equal(lib.gatewayProvidersAbsent(configs({}), 'org', ['typesafe']), true);
  assert.equal(lib.gatewayProvidersAbsent({ data: {} }, 'org', ['typesafe']), true);
  // Go's providers,omitempty omits an empty map after the last deletion.
  assert.equal(lib.gatewayProvidersAbsent({ data: { org: {} } }, 'org', ['typesafe']), true);
  for (const invalid of [null, {}, { data: null }, { data: [] }, { data: { org: null } }, configs(null)]) {
    assert.equal(lib.gatewayProvidersAbsent(invalid, 'org', ['typesafe']), false);
  }
  assert.equal(lib.gatewayProvidersAbsent(configs({ typesafe: { api_key: 'changed-mask' } }), 'org', ['typesafe']), false);
});

for (const fails of [false, true]) {
  test(`finally path after ${fails ? 'injected failure' : 'success'} records independent cleanup`, async () => {
    const f = fixture(), original = new Error('injected body failure');
    let recorded;
    const running = lib.withTypeSafeTeardown({
      run: async () => { if (fails) throw original; },
      teardown: () => lib.teardownTypeSafe(f.resources, f.io),
      record: result => { recorded = result; },
    });
    if (fails) await assert.rejects(running, e => e === original);
    else await running;
    assert.equal(recorded.testVerdict, fails ? 'FAIL' : 'PASS');
    assert.equal(recorded.teardown.verdict, 'PASS');
    assert.deepEqual(f.revoked, ['k1']);
  });
}

test('teardown and report failures cannot replace the original test exception', async () => {
  const original = new Error('original');
  let recorded;
  await assert.rejects(lib.withTypeSafeTeardown({ run: async () => { throw original; },
    teardown: async () => { throw new Error('secret cleanup error'); },
    record: result => { recorded = result; throw new Error('write failed'); },
  }), e => e === original);
  assert.equal(recorded.testVerdict, 'FAIL');
  assert.equal(recorded.teardown.verdict, 'FAIL');
  assert.doesNotMatch(JSON.stringify(recorded), /secret/);
});

test('cleanup failure fails an otherwise successful run without relabeling the test body', async () => {
  let recorded;
  await assert.rejects(lib.withTypeSafeTeardown({ run: async () => {},
    teardown: async () => ({ verdict: 'FAIL', checks: [] }), record: result => { recorded = result; },
  }), /teardown failed/);
  assert.equal(recorded.testVerdict, 'PASS');
  assert.equal(recorded.teardown.verdict, 'FAIL');
});

test('soft test failures retain a FAIL test result independently of successful cleanup', async () => {
  let recorded;
  await lib.withTypeSafeTeardown({ run: async () => {}, failed: () => true,
    teardown: async () => ({ verdict: 'PASS', checks: [] }), record: result => { recorded = result; },
  });
  assert.equal(recorded.testVerdict, 'FAIL');
  assert.equal(recorded.teardown.verdict, 'PASS');
});

test('preset report names the missing jev-1.14.0 cause, not an upstream failure', () => {
  assert.match(lib.presetGap(['jev-latest', 'jev-1.13.0']), /preset gap.*jev-1\.14\.0.*red by design/i);
  assert.equal(lib.presetGap(['jev-latest', 'jev-1.14.0']), undefined);
});
