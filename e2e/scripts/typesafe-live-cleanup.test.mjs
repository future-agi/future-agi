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
  const name = 'e2e-typesafe-exact-run-suffix';
  const key = (id, org = 'owned', keyName = name) => ({ id, name: keyName, status: 'active', metadata: { org_id: org } });
  const baseline = key('baseline');
  const gateway = [baseline, key('orphan'), key('other-org', 'other'), key('lookalike', 'owned', name + '-other')];
  const resources = lib.createTypeSafeResources([], [], { organizationId: 'owned', gatewayKeys: [baseline] });
  resources.keyNames.add(name);
  resources.limits = { backend: { id: 'original', rate_limiting: { per_key_rpm: null }, budgets: {} }, gateway: {} };
  const original = structuredClone(resources.limits);
  let backend = { id: 'temporary', rate_limiting: { enabled: true, per_key_rpm: 1 }, budgets: {} };
  let gatewayLimits = { rate_limiting: { enabled: true, per_key_rpm: 1 } };
  const events = [];
  const io = {
    listProviders: async () => [], listKeys: async () => [],
    listGatewayKeys: async () => gateway,
    deleteProvider: async () => {}, revokeKey: async () => { throw new Error('No backend row exists'); },
    revokeGatewayKey: async id => { events.push('revoke:' + id); gateway.find(k => k.id === id).status = 'revoked'; },
    gatewayProvidersAbsent: async () => true,
    gatewayKeyRevoked: async key => gateway.find(k => k.id === key.gateway_key_id)?.status === 'revoked',
    restoreLimits: async snapshot => { events.push('restore'); backend = structuredClone(snapshot.backend); gatewayLimits = structuredClone(snapshot.gateway); },
    backendLimitsRestored: async snapshot => { events.push('verify-backend'); return JSON.stringify(backend) === JSON.stringify(snapshot.backend); },
    gatewayLimitsRestored: async snapshot => { events.push('verify-gateway'); return JSON.stringify(gatewayLimits) === JSON.stringify(snapshot.gateway); },
    waitUntil: async check => { if (!await check()) throw new Error('private-verification-detail'); },
  };
  return { resources, io, gateway, events, original, limits: () => ({ backend, gateway: gatewayLimits }) };
}

test('gateway-only orphan is revoked with no Django row, exact org/name and both baselines preserved; cleanup is idempotent', async () => {
  const f = fixture();
  for (let n = 0; n < 2; n++) {
    assert.equal((await lib.teardownTypeSafe(f.resources, f.io)).verdict, 'PASS');
    assert.deepEqual(f.gateway.map(k => k.status), ['active', 'revoked', 'active', 'active']);
  }
  assert.deepEqual(f.events.filter(e => e.startsWith('revoke:')), ['revoke:orphan']);
});

test('restore baseline limits after the last provider push, and verify both stores independently', async () => {
  const f = fixture();
  f.resources.providerNames.add('typesafe');
  let providers = [{ id: 'run', provider_name: 'typesafe' }];
  f.io.listProviders = async () => providers;
  f.io.deleteProvider = async () => { f.events.push('provider-push'); providers = []; };
  assert.equal((await lib.teardownTypeSafe(f.resources, f.io)).verdict, 'PASS');
  assert.deepEqual(f.limits(), f.original);
  assert.ok(f.events.indexOf('restore') > f.events.indexOf('provider-push'));
  assert.ok(f.events.indexOf('verify-backend') > f.events.indexOf('restore'));
  assert.ok(f.events.indexOf('verify-gateway') > f.events.indexOf('restore'));
});

for (const operation of ['listGatewayKeys', 'restoreLimits', 'backendLimitsRestored', 'gatewayLimitsRestored']) {
  test(`${operation} failure is explicit teardown FAIL without private response details`, async () => {
    const f = fixture();
    f.io[operation] = async () => { throw new Error('private-response-sentinel'); };
    const result = await lib.teardownTypeSafe(f.resources, f.io);
    assert.equal(result.verdict, 'FAIL');
    assert.doesNotMatch(JSON.stringify(result), /private-response/);
  });
}

for (const invalid of [null, {}, { data: null }, { data: [null] }, { data: [{ id: 'x', name: 'n', status: 'active', metadata: { org_id: 1 } }] }]) {
  test('gateway inventory rejects invalid envelope/ownership metadata', () => {
    assert.throws(() => lib.gatewayKeyInventory(invalid), /inventory/);
  });
}

test('gateway inventory allows omitted metadata on unrelated keys and strips all non-cleanup fields', () => {
  assert.deepEqual(lib.gatewayKeyInventory({ data: [{ id: 'x', name: 'n', status: 'active', key_prefix: 'private', metadata: { org_id: 'owned' } },
    { id: 'y', name: 'unscoped', status: 'active' }] }), [
    { id: 'x', name: 'n', status: 'active', metadata: { org_id: 'owned' } },
    { id: 'y', name: 'unscoped', status: 'active', metadata: {} },
  ]);
});

test('gateway missing org metadata cannot prove ownership even with an exact intended name', async () => {
  const f = fixture();
  delete f.gateway[1].metadata.org_id;
  const result = await lib.teardownTypeSafe(f.resources, f.io);
  assert.equal(result.verdict, 'FAIL');
  assert.equal(f.gateway[1].status, 'active');
});
