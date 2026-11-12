import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { spawnSync } from 'node:child_process';
import { setImmediate as turn } from 'node:timers/promises';
import test from 'node:test';
import { SamlRequestRecorder, redactReceipts, withSamlRecorders } from '../lib/saml-request-recorder.ts';

const ACS = '/saml2_auth/acs/';
const COMPLETE = '/saml2_auth/complete/';
const USER_INFO = '/accounts/user-info/';
const COOKIE = 'fai_saml_b_fixture=synthetic-cookie-canary';
const BEARER = 'synthetic-bearer-canary';
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function request(page, pathname, headers, method = 'GET') {
  let reads = 0;
  page.emit('request', {
    url: () => `https://example.test${pathname}?c=synthetic-query-canary`,
    method: () => method,
    headers: () => { throw new Error('headers() would omit cookie evidence'); },
    allHeaders: () => { reads++; return headers; },
  });
  return () => reads;
}
function observe(promise) {
  let settled = false;
  promise.then(() => { settled = true; }, () => { settled = true; });
  return () => settled;
}

test('drain and stop await deferred headers before context closure', async () => {
  const page = new EventEmitter();
  const recorder = new SamlRequestRecorder(page, 'SAML-E2E-001');
  const headers = deferred();
  const reads = request(page, ACS, headers.promise, 'POST');
  assert.equal(reads(), 1);
  const draining = recorder.drain();
  const stopped = recorder.stop();
  const drained = observe(draining);
  let closed = false;
  const teardown = stopped.then(() => { closed = true; });
  await turn();
  assert.equal(drained(), false);
  assert.equal(closed, false);
  headers.resolve({ cookie: COOKIE });
  assert.equal((await draining)[0].cookieHeader, COOKIE);
  assert.equal((await stopped).length, 1);
  await teardown;
  assert.equal(closed, true);
  assert.equal(request(page, COMPLETE, Promise.resolve({}))(), 0);
});

test('drain includes a second request arriving during its first batch', async () => {
  const page = new EventEmitter();
  const recorder = new SamlRequestRecorder(page, 'SAML-E2E-001');
  const a = deferred(), b = deferred();
  request(page, ACS, a.promise);
  const draining = recorder.drain();
  const settled = observe(draining);
  request(page, COMPLETE, b.promise);
  a.resolve({});
  await turn();
  assert.equal(settled(), false, 'the second pending batch still belongs to this drain');
  b.resolve({});
  assert.deepEqual((await draining).map(row => row.path), [ACS, COMPLETE]);
  // A snapshot must not detach: receipt-presence polling needs later user-info.
  request(page, USER_INFO, Promise.resolve({}));
  assert.equal((await recorder.drain()).length, 3);
  await recorder.stop();
});

test('stop detaches only its listener immediately and is idempotent', async () => {
  const page = new EventEmitter();
  let unrelatedCalls = 0;
  const unrelated = () => { unrelatedCalls++; };
  page.on('request', unrelated);
  const recorder = new SamlRequestRecorder(page, 'SAML-E2E-003');
  const headers = deferred();
  request(page, COMPLETE, headers.promise);
  const stopped = recorder.stop();
  assert.deepEqual(page.listeners('request'), [unrelated]);
  assert.equal(recorder.stop(), stopped);
  assert.equal(request(page, USER_INFO, Promise.resolve({}))(), 0);
  assert.equal(unrelatedCalls, 2);
  headers.resolve({});
  assert.equal((await stopped).length, 1);
  assert.equal((await recorder.stop()).length, 1);
});

test('X and Y own independent queues, receipts and failures', async () => {
  const x = new EventEmitter(), y = new EventEmitter();
  const rx = new SamlRequestRecorder(x, 'SAML-E2E-002-X');
  const ry = new SamlRequestRecorder(y, 'SAML-E2E-002-Y');
  const hx = deferred(), hy = deferred();
  request(x, ACS, hx.promise);
  request(y, COMPLETE, hy.promise);
  hx.reject(new Error(COOKIE));
  await assert.rejects(rx.stop(), /SAML-E2E-002-X GET \/saml2_auth\/acs\//);
  assert.equal(y.listenerCount('request'), 1);
  const drainingY = ry.drain();
  const settledY = observe(drainingY);
  await turn();
  assert.equal(settledY(), false);
  hy.resolve({});
  assert.deepEqual((await drainingY).map(row => row.flow), ['SAML-E2E-002-Y']);
  await ry.stop();
});

for (const kind of ['rejected read', 'receipt construction', 'synchronous read']) {
  test(`${kind} is owned immediately and later fails drain/stop with safe diagnostics`, () => {
    const moduleUrl = new URL('../lib/saml-request-recorder.ts', import.meta.url).href;
    const child = spawnSync(process.execPath, ['--unhandled-rejections=strict', '--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import { EventEmitter } from 'node:events';
      import { setImmediate as turn } from 'node:timers/promises';
      import { SamlRequestRecorder } from ${JSON.stringify(moduleUrl)};
      const secret = 'synthetic-error-cookie-bearer-query-canary';
      const page = new EventEmitter();
      const recorder = new SamlRequestRecorder(page, 'SAML-E2E-001');
      page.emit('request', {
        url: () => 'https://example.test/saml2_auth/acs/?c=' + secret,
        method: () => 'POST',
        allHeaders: () => {
          if (${JSON.stringify(kind)} === 'synchronous read') throw new Error(secret);
          if (${JSON.stringify(kind)} === 'receipt construction') {
            return Promise.resolve({ cookie: { split() { throw new Error(secret); } } });
          }
          return Promise.reject(new Error(secret));
        },
      });
      // No rejection handler is installed by the caller until another turn.
      await turn();
      await turn();
      for (const operation of [() => recorder.drain(), () => recorder.stop()]) {
        await assert.rejects(operation, error => {
          assert.match(error.message, /SAML-E2E-001 POST \\/saml2_auth\\/acs\\//);
          assert.equal(String(error).includes(secret), false);
          assert.equal(JSON.stringify(error.errors).includes(secret), false);
          assert.equal(String(error).includes('https://'), false);
          return true;
        });
      }
      assert.deepEqual(recorder.redactedSnapshot(), []);
      await turn();
    `], { encoding: 'utf8' });
    assert.equal(child.status, 0, child.stderr || child.stdout);
    assert.doesNotMatch(child.stderr, /UnhandledPromiseRejection|PromiseRejectionHandledWarning/);
  });
}

test('event-time ordering survives reverse header resolution and preserves receipt fields', async t => {
  let clock = 99;
  t.mock.method(Date, 'now', () => clock);
  const page = new EventEmitter();
  const recorder = new SamlRequestRecorder(page, 'SAML-E2E-006');
  const old = deferred(), first = deferred(), second = deferred();
  request(page, USER_INFO, old.promise);
  const completedAt = 100;
  clock = 101;
  request(page, USER_INFO, first.promise);
  clock = 102;
  request(page, USER_INFO, second.promise);
  second.resolve({ authorization: 'Bearer later' });
  await turn();
  first.resolve({ cookie: `z=last; ${COOKIE}; a=first`, authorization: `Bearer ${BEARER}`,
    'x-organization-id': '', 'x-workspace-id': '' });
  old.resolve({ authorization: 'Bearer old' });
  const rows = await recorder.drain();
  assert.equal(rows[0].at, 102, 'resolution order differs from emission order');
  const selected = rows.filter(row => row.at >= completedAt).sort((a, b) => a.at - b.at)[0];
  assert.deepEqual(selected, {
    flow: 'SAML-E2E-006', at: 101, method: 'GET', path: USER_INFO,
    cookieNames: ['a', 'fai_saml_b_fixture', 'z'], cookieHeader: `z=last; ${COOKIE}; a=first`,
    authorizationPrefix: 'Bearer', authorizationValue: BEARER,
    hasOrganizationHeader: true, hasWorkspaceHeader: true,
  });
  // A later deliberately scoped request must not replace the first assertion anchor.
  clock = 103;
  request(page, USER_INFO, Promise.resolve({ 'x-organization-id': 'old-org' }));
  assert.equal((await recorder.stop()).filter(row => row.at >= completedAt)
    .sort((a, b) => a.at - b.at)[0].authorizationValue, BEARER);
});

test('filters exact paths, keeps allHeaders binder evidence and redacts only secrets', async () => {
  const page = new EventEmitter();
  const recorder = new SamlRequestRecorder(page, 'SAML-E2E-003');
  for (const pathname of ['/assets/file.js', '/saml2_auth/idp-login/', '/accounts/user-info/extra']) {
    assert.equal(request(page, pathname, Promise.resolve({}))(), 0);
  }
  request(page, ACS, Promise.resolve({}), 'POST');
  request(page, COMPLETE, Promise.resolve({ cookie: COOKIE, authorization: `Bearer ${BEARER}` }));
  const rows = await recorder.stop();
  assert.deepEqual(rows[0].cookieNames, []);
  assert.deepEqual(rows[1].cookieNames, ['fai_saml_b_fixture']);
  assert.equal(rows[1].cookieHeader, COOKIE);
  assert.equal(rows[1].authorizationValue, BEARER);
  assert.equal(rows[0].hasOrganizationHeader, false);
  assert.equal(rows[0].hasWorkspaceHeader, false);
  const redacted = redactReceipts(rows);
  assert.deepEqual(redacted[0], rows[0]);
  assert.deepEqual(redacted[1], { ...rows[1], cookieHeader: '[REDACTED]', authorizationValue: '[REDACTED]' });
  for (const secret of [COOKIE, BEARER, 'synthetic-query-canary']) {
    assert.equal(JSON.stringify(redacted).includes(secret), false);
  }
  assert.deepEqual(recorder.redactedSnapshot(), redacted);
});

test('failure cleanup waits for both recorders, releases all resources and retains primary failure', async () => {
  const x = new EventEmitter(), y = new EventEmitter();
  const hx = deferred(), hy = deferred();
  const primary = new Error('original assertion failure');
  const released = [];
  let evidence;
  const run = withSamlRecorders({
    report: async value => { evidence = value; },
    release: ['browser X', 'browser Y', 'IdP', 'probe'].map(name => ({ name, run: async () => {
      assert.equal(x.listenerCount('request'), 0);
      assert.equal(y.listenerCount('request'), 0);
      released.push(name);
      if (name === 'IdP') throw new Error(COOKIE);
    } })),
  }, async record => {
    record(x, 'SAML-E2E-002-X');
    record(y, 'SAML-E2E-002-Y');
    request(x, ACS, hx.promise);
    request(y, COMPLETE, hy.promise);
    throw primary;
  });
  observe(run);
  hx.reject(new Error(BEARER));
  await turn();
  assert.equal(x.listenerCount('request'), 0);
  assert.equal(y.listenerCount('request'), 0);
  assert.deepEqual(released, [], 'one rejected stop must not close the other context early');
  hy.resolve({ cookie: COOKIE });
  await assert.rejects(run, error => {
    assert.equal(error.cause, primary);
    assert.equal(error.errors[0], primary);
    assert.match(error.message, /SAML-E2E-002-X/);
    assert.match(error.message, /cleanup failed: IdP/);
    return true;
  });
  assert.deepEqual(released.sort(), ['IdP', 'browser X', 'browser Y', 'probe']);
  assert.equal(evidence.receipts[0].cookieHeader, '[REDACTED]');
  assert.equal(evidence.errors.length, 2);
  for (const secret of [COOKIE, BEARER]) assert.equal(JSON.stringify(evidence).includes(secret), false);
});

test('a primary failure without cleanup errors is rethrown unchanged', async () => {
  const primary = new Error('assertion failure');
  let reported = false, released = false;
  await assert.rejects(withSamlRecorders({
    report: async () => { reported = true; },
    release: [{ name: 'probe', run: async () => { released = true; } }],
  }, async () => { throw primary; }), error => error === primary);
  assert.equal(reported, true);
  assert.equal(released, true);
});

test('recording failure alone fails the owning scope, even if assertions did not drain', async () => {
  const page = new EventEmitter();
  await assert.rejects(withSamlRecorders({ report: async () => {}, release: [] }, async record => {
    record(page, 'SAML-E2E-001');
    request(page, ACS, Promise.reject(new Error(COOKIE)));
    await turn();
  }), /SAML recording failed: SAML-E2E-001/);
});

test('attachment failure cannot prevent resources from being released', async () => {
  const released = [];
  await assert.rejects(withSamlRecorders({
    report: async () => { throw new Error(BEARER); },
    release: ['context', 'IdP', 'probe'].map(name => ({ name, run: async () => { released.push(name); } })),
  }, async () => {}), /receipt attachment/);
  assert.deepEqual(released, ['context', 'IdP', 'probe']);
});
