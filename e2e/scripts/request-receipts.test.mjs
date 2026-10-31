import assert from 'node:assert/strict';
import test from 'node:test';
import { acquireReceipt } from '../lib/request-receipts.ts';

const bearer = 'access-token-fixture';

function state(overrides = {}) {
  return {
    phase: 'browser setup',
    method: 'GET',
    pathname: '/accounts/config/',
    search: '',
    afterPrime: false,
    originalBearer: bearer,
    sawResponse: false,
    finished: false,
    navigationSuperseded: false,
    failureErrorText: null,
    headersSettled: false,
    headersRejected: false,
    headers: null,
    ...overrides,
  };
}

test('pending header promise on a navigation-superseded request does not wait', async () => {
  let read = false;
  const pending = new Promise(() => {});
  const started = Date.now();
  const receipt = await acquireReceipt(state({ navigationSuperseded: true }), () => {
    read = true;
    return pending;
  });
  assert.equal(read, false);
  assert.ok(Date.now() - started < 200);
  assert.equal(receipt.lifecycle, 'canceled-navigation');
  assert.equal(receipt.headerReadFailed, false);
  assert.equal(receipt.headersRead, false);
  assert.equal(receipt.sameBearer, false);
  assert.equal(receipt.path, '/accounts/config/');
  assert.equal(receipt.organization, null);
  assert.equal(receipt.workspace, null);
});

test('canceled navigation is a receipt, not a skipped or suppressed error', async () => {
  const receipt = await acquireReceipt(
    state({ failureErrorText: 'net::ERR_ABORTED', phase: 'A1 baseline', method: 'GET' }),
    () => { throw new Error('must not read headers after an abort'); },
  );
  assert.equal(receipt.lifecycle, 'canceled-navigation');
  assert.equal(receipt.headerReadFailed, false);
  assert.equal(receipt.headersRead, false);
  assert.equal(receipt.sameBearer, false);
  assert.equal(receipt.phase, 'A1 baseline');
});

test('a real readable protected request uses the complete allHeaders map', async () => {
  const receipt = await acquireReceipt(state({
    phase: 'implicit and explicit controls',
    pathname: '/tracer/project/list_projects/',
    search: '?project_type=observe&page_number=0&page_size=25',
    afterPrime: true,
    sawResponse: true,
    finished: true,
  }), async () => ({
    authorization: `Bearer ${bearer}`,
    'x-organization-id': 'org-1',
    'x-workspace-id': 'ws-2',
  }));
  assert.equal(receipt.lifecycle, 'complete');
  assert.equal(receipt.headersRead, true);
  assert.equal(receipt.headerReadFailed, false);
  assert.equal(receipt.sameBearer, true);
  assert.equal(receipt.organization, 'org-1');
  assert.equal(receipt.workspace, 'ws-2');
  assert.equal(receipt.workspaceQuery, null);
  assert.equal(receipt.afterPrime, true);
  assert.equal(Object.hasOwn(receipt, 'authorization'), false);
});

test('a connection failure is unreadable, not reclassified as a cancel', async () => {
  const receipt = await acquireReceipt(
    state({ failureErrorText: 'net::ERR_CONNECTION_REFUSED' }),
    () => { throw new Error('must not treat a transport error as canceled navigation'); },
  );
  assert.equal(receipt.lifecycle, 'unreadable');
  assert.equal(receipt.headerReadFailed, true);
  assert.equal(receipt.headersRead, false);
});

test('a response still requires complete headers even after a later navigation mark', async () => {
  const receipt = await acquireReceipt(state({
    sawResponse: true,
    navigationSuperseded: true,
    pathname: '/accounts/user-info/',
    afterPrime: true,
  }), async () => ({
    authorization: `Bearer ${bearer}`,
    'x-organization-id': 'org-1',
    'x-workspace-id': 'ws-2',
  }));
  assert.equal(receipt.lifecycle, 'complete');
  assert.equal(receipt.headersRead, true);
  assert.equal(receipt.sameBearer, true);
});
