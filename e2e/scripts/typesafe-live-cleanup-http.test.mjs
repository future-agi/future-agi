import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { registerHooks } from 'node:module';
import test from 'node:test';
import ts from 'typescript';
const live = new URL('../lib/typesafe-live.ts', import.meta.url).href;
const cleanup = new URL('../lib/typesafe-cleanup.ts', import.meta.url).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === './typesafe-live' && context.parentURL === cleanup) return { url: live, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    return ![live, cleanup].includes(url) ? loaded : { ...loaded, format: 'module', source: ts.transpileModule(String(loaded.source), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    }).outputText };
  },
});
const { createTypeSafeResources, teardownTypeSafe } = await import(live);
const { createTypeSafeCleanupIO } = await import(cleanup);

async function fixture(t, baselineGateway = {}) {
  const baseline = { id: 'original', organization: 'owned', is_active: true,
    rate_limiting: { per_key_rpm: null }, budgets: {}, routing: { strategy: 'round_robin' },
    cache: { enabled: true }, cost_tracking: { enabled: true } };
  const state = { active: structuredClone(baseline), gateway: structuredClone(baselineGateway), keys: [], providers: [], requests: [],
    malformedInventory: false, unreachable: false, resetFailure: false };
  const server = createServer(async (req, res) => {
    const url = req.url, method = req.method;
    state.requests.push([method, url]);
    const send = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
    if (url.startsWith('/-/') && req.headers.authorization !== 'Bearer synthetic-admin') return send({}, 403);
    if (url.startsWith('/agentcc/') && req.headers['x-organization-id'] !== 'owned') return send({}, 403);
    if (url === '/-/keys' && method === 'GET') return send(state.malformedInventory ? { data: null } : { data: state.keys });
    if (url.startsWith('/-/keys/') && method === 'DELETE') {
      const key = state.keys.find(k => k.id === decodeURIComponent(url.split('/').pop()));
      if (!key) return send({ error: { code: 'key_not_found' } }, 404);
      key.status = 'revoked'; return send({ status: 'revoked' });
    }
    if (url === '/agentcc/api-keys/') return send({ result: [] });
    if (url === '/agentcc/provider-credentials/') return send({ result: state.providers });
    if (url.startsWith('/agentcc/provider-credentials/') && method === 'DELETE') {
      state.providers = []; state.gateway = { rate_limiting: state.active.rate_limiting, budgets: state.active.budgets };
      return send({ result: { deleted: true } });
    }
    if (url === '/-/orgs/configs') {
      if (state.unreachable) return req.socket.destroy();
      return send({ data: { owned: state.gateway } });
    }
    if (url === '/agentcc/org-configs/active/') return send({ result: state.active });
    if (url === '/agentcc/org-configs/' && method === 'POST') {
      let input = ''; for await (const chunk of req) input += chunk;
      state.active = { ...JSON.parse(input), id: 'temporary', organization: 'owned', is_active: true };
      state.gateway = { rate_limiting: state.active.rate_limiting, budgets: state.active.budgets };
      return send({ result: { ...state.active, gateway_synced: true } });
    }
    if (url === '/agentcc/org-configs/original/activate/' && method === 'POST') {
      if (state.resetFailure) return send({ error: 'private-response-sentinel' }, 500);
      if (state.beforeActivation) await state.beforeActivation();
      state.active = structuredClone(baseline); state.gateway = structuredClone(baselineGateway);
      return send({ result: { ...state.active, gateway_synced: true } });
    }
    send({}, 404);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const io = (signal, drainWrites = false) => createTypeSafeCleanupIO({ apiUrl: base, gatewayUrl: base, accessToken: 'synthetic-token',
    organizationId: 'owned', workspaceId: 'owned-workspace', adminToken: 'synthetic-admin', signal, drainWrites });
  return { state, io, baseline };
}

for (const gateway of [{}, { rate_limiting: null, budgets: null }, { rate_limiting: { enabled: false }, budgets: { enabled: false } }]) {
  test(`HTTP snapshot retains exact omitted/null/explicit gateway limits: ${JSON.stringify(gateway)}`, async t => {
    const f = await fixture(t, gateway), body = new AbortController();
    const bodyIO = f.io(body.signal);
    const resources = createTypeSafeResources([], [], { organizationId: 'owned', gatewayKeys: await bodyIO.listGatewayKeys() });
    resources.limits = await bodyIO.captureLimits();
    assert.deepEqual(resources.limits.gateway, gateway);
    await bodyIO.changeLimits({ rate_limiting: { enabled: true, per_key_rpm: 1 } });
    assert.deepEqual(f.state.active.routing, f.baseline.routing, 'POST must preserve unrelated config fields');
    assert.deepEqual(f.state.active.cache, f.baseline.cache);
    body.abort();
    await assert.rejects(bodyIO.restoreLimits(resources.limits));
    f.state.providers.push({ id: 'run', provider_name: 'typesafe' }); resources.providerNames.add('typesafe');
    const result = await teardownTypeSafe(resources, f.io(new AbortController().signal));
    assert.equal(result.verdict, 'PASS');
    assert.deepEqual(f.state.active, f.baseline);
    assert.deepEqual(f.state.gateway, gateway);
    const lastDelete = f.state.requests.findIndex(([m]) => m === 'DELETE');
    const activation = f.state.requests.findIndex(([, p]) => p.endsWith('/activate/'));
    assert.ok(activation > lastDelete);
  });
}

test('HTTP gateway-only inventory revokes owned orphan and preserves another org with the exact name', async t => {
  const f = await fixture(t), io = f.io(), name = 'e2e-typesafe-exact-suffix';
  const resources = createTypeSafeResources([], [], { organizationId: 'owned', gatewayKeys: [] });
  resources.keyNames.add(name);
  f.state.keys.push(...['owned', 'other'].map((org, i) => ({ id: 'key' + i, name, status: 'active', metadata: { org_id: org } })));
  for (let i = 0; i < 2; i++) assert.equal((await teardownTypeSafe(resources, io)).verdict, 'PASS');
  assert.deepEqual(f.state.keys.map(k => k.status), ['revoked', 'active']);
});

for (const fault of ['malformedInventory', 'resetFailure', 'unreachable']) {
  test(`HTTP ${fault} returns explicit redacted teardown FAIL`, async t => {
    const f = await fixture(t), io = f.io();
    const resources = createTypeSafeResources([], [], { organizationId: 'owned', gatewayKeys: [] });
    resources.limits = await io.captureLimits();
    await io.changeLimits({ budgets: { enabled: true, org_limit: 0.000001 } });
    f.state[fault] = true;
    // Keep negative unit checks fast; production's bounded poll is exercised by
    // the real Playwright fixture tests, not by waiting two minutes per mismatch.
    io.waitUntil = async check => { if (!await check()) throw new Error('verification failed'); };
    const result = await teardownTypeSafe(resources, io);
    assert.equal(result.verdict, 'FAIL');
    assert.doesNotMatch(JSON.stringify(result), /private-response/);
  });
}

test('an accepted body restore drains its HTTP response before cancellation can start inventory', async t => {
  const f = await fixture(t), body = new AbortController(), io = f.io(body.signal, true);
  const snapshot = await io.captureLimits();
  await io.changeLimits({ rate_limiting: { per_key_rpm: 1 } });
  let release, accepted;
  const pending = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { accepted = resolve; });
  f.state.beforeActivation = async () => { accepted(); await pending; };
  let settled = false;
  const restoring = io.restoreLimits(snapshot).finally(() => { settled = true; });
  const rejected = assert.rejects(restoring, /details withheld/);
  await started; body.abort();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(settled, false, 'cancel must not abandon an accepted config write');
  release(); await rejected;
  assert.equal(settled, true);
  assert.deepEqual(f.state.active, f.baseline);
});

for (const row of [null, {}, { id: 'original', organization: 'other', is_active: true, rate_limiting: {}, budgets: {} }]) {
  test('absent/malformed/cross-org active snapshot fails before any config mutation', async t => {
    const f = await fixture(t), io = f.io();
    f.state.active = row;
    await assert.rejects(io.captureLimits(), /snapshot unavailable/);
    await assert.rejects(io.changeLimits({ rate_limiting: { per_key_rpm: 1 } }), /snapshot unavailable/);
    assert.ok(f.state.requests.every(([method]) => method === 'GET'));
  });
}
