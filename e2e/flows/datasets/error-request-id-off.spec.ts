import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Separate OFF runtime: default setting, errors.requestId unclaimed.
// Do not run together with DATA-E2E-176 in the same frontend/backend build.
const LIST = '/model-hub/develops/get-datasets/';
const UI_READY = 60_000;
const offHost = (url: URL) => /^https?:$/.test(url.protocol)
  && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);

test('DATA-E2E-177: an unclaimed error does not expose or show a request id', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-177', area: 'datasets',
    userGoal: 'Retry a dataset error without activating the hidden request-id surface',
    steps: [
      'open Datasets in each of two synthetic organizations with the setting OFF and the claim absent',
      'change only the real list request method to produce a Django 405 error',
      'verify the wire still has a request id but browser JavaScript cannot read it and no Request id row renders',
      'restore GET and retry successfully without any dataset writes',
    ],
    backendChecks: [
      'both direct cross-origin callers receive 405 with a wire X-Request-ID and no Access-Control-Expose-Headers',
      'browser fetch sees no X-Request-ID while credentials and allowed-origin CORS continue working',
      'retry returns 200 for each tenant and creates no dataset',
    ],
  }),
}, async ({ browser, scopeActors: scopes }) => {
  test.setTimeout(360_000);
  const ui = expect.configure({ timeout: UI_READY });
  for (const actor of [scopes.ownerA, scopes.ownerB]) {
    const context = await scopes.openContext(browser, actor);
    await context.route(offHost, route => route.abort());
    let refuse = true;
    await context.route(url => url.pathname === LIST, route => route.continue(
      refuse ? { method: 'PATCH', postData: '{}' } : {}));
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const wirePromise = page.waitForResponse(response => new URL(response.url()).pathname === LIST
      && response.status() === 405);
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    const wire = await wirePromise;
    expect(wire.headers()['x-request-id']).toBeTruthy();
    expect(wire.headers()['access-control-expose-headers']).toBeUndefined();
    const error = page.getByTestId('error-state');
    await ui(error).toBeVisible();
    await ui(error.getByText('Request id', { exact: true })).toHaveCount(0);
    await ui(error.getByRole('button', { name: 'Copy request id' })).toHaveCount(0);
    expect(new URL(E2E.apiUrl).origin).not.toBe(new URL(page.url()).origin);
    const direct = await page.evaluate(async ({ url, headers }) => {
      const response = await fetch(url, { method: 'PATCH', headers, body: '{}' });
      return { status: response.status, id: response.headers.get('X-Request-ID') };
    }, { url: `${E2E.apiUrl}${LIST}`, headers: { ...scopes.headers(actor), 'Content-Type': 'application/json' } });
    expect(direct).toEqual({ status: 405, id: null });
    refuse = false;
    const retry = page.waitForResponse(response => new URL(response.url()).pathname === LIST
      && response.status() === 200 && response.request().method() === 'GET');
    await error.getByRole('button', { name: /^Retry/ }).click();
    const response = await retry;
    expect((await response.json()).result.datasets).toEqual([]);
    await ui(page.getByTestId('error-state')).toHaveCount(0);
    await context.close();
  }
});
