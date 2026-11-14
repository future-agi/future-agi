import { test, expect } from '@playwright/test';
import { captureAnnotationHttp } from '../lib/annotation-http';

const ORIGIN = 'http://localhost', BULK = '/model-hub/scores/bulk/';

test('annotation recorder preserves scope, inputs and bodies and detaches cleanly', async ({ context, page }) => {
  const foreignPage = await context.newPage();
  // Intercept every request: this harness test needs no backend or database.
  await context.route('**/*', route => route.fulfill({
    contentType: new URL(route.request().url()).pathname === '/' ? 'text/html' : 'application/json',
    body: new URL(route.request().url()).pathname === '/' ? '<html></html>' : '{"result":{"errors":[]}}',
    headers: { 'access-control-allow-origin': '*', 'x-request-id': 'receipt-test' },
  }));
  const heads: unknown[] = [];
  const capture = captureAnnotationHttp([page, foreignPage], ORIGIN, async (_, body) => { heads.push(body); });
  for (const [surface, workspace] of [[page, 'primary'], [foreignPage, 'foreign']] as const) {
    await surface.goto(ORIGIN);
    await surface.evaluate(async ({ workspace, path }) => {
      await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json',
        authorization: 'Bearer synthetic', 'x-organization-id': 'org', 'x-workspace-id': workspace },
      body: JSON.stringify({ workspace }) });
    }, { workspace, path: BULK });
  }
  await page.evaluate(async () => {
    await fetch('/tracer/dashboard/metrics/?search=label');
    await fetch('/ignored/');
    await fetch('/tracer/dashboard/query/', { method: 'DELETE' });
    await fetch('http://foreign.invalid/tracer/dashboard/metrics/');
  });
  await expect.poll(() => capture.receipts.filter(row => row.settled).length).toBe(3);
  await Promise.all(capture.pending);
  expect(capture.receipts.slice(0, 2).map(row => ({ input: row.input, scope: row.scope, authorized: row.authorized })))
    .toEqual(['primary', 'foreign'].map(workspace => ({ input: { workspace },
      scope: { organizationId: 'org', workspaceId: workspace }, authorized: true })));
  expect(capture.receipts[2]).toMatchObject({ input: { search: 'label' }, authorized: false });
  for (const receipt of capture.receipts) {
    expect(receipt).toMatchObject({ status: 200, body: { result: { errors: [] } }, requestId: 'receipt-test', settled: true });
    expect(receipt.error).toBeUndefined();
    expect(receipt.endedAt).toBeGreaterThanOrEqual(receipt.startedAt);
  }
  expect(heads).toHaveLength(3);
  capture.stop();
  await page.evaluate(() => fetch('/tracer/dashboard/metrics/'));
  expect(capture.receipts).toHaveLength(3);
});

for (const scenario of [
  { name: 'failed score', body: '{"result":{"errors":["rejected"]}}', error: 'bulk_score_errors', status: 200 },
  { name: 'missing score result', body: '{}', error: 'bulk_score_errors', status: 200 },
  { name: 'application failure', body: '{"status":false,"result":{"errors":[]}}', error: 'application_error', status: 200 },
  { name: 'malformed JSON', body: '{', error: 'response_json_unreadable', status: 200 },
  { name: 'HTML error', body: '<html>error</html>', contentType: 'text/html', error: 'non_json_body_omitted', status: 503 },
  { name: 'transport failure', body: '', error: 'request_failed', status: 0 },
]) {
  test(`annotation recorder retains ${scenario.name} before a later success`, async ({ context, page }) => {
    let calls = 0;
    await context.route('**/*', route => {
      if (new URL(route.request().url()).pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<html></html>' });
      if (calls++ > 0) return route.fulfill({ contentType: 'application/json', body: '{"result":{"errors":[]}}' });
      if (scenario.status === 0) return route.abort();
      return route.fulfill({ status: scenario.status, contentType: scenario.contentType ?? 'application/json', body: scenario.body });
    });
    const capture = captureAnnotationHttp([page], ORIGIN, async () => {});
    await page.goto(ORIGIN);
    for (let i = 0; i < 2; i++) {
      await page.evaluate(async path => {
        try { await fetch(path, { method: 'POST', body: '{}' }); } catch { /* Expected transport failure. */ }
      }, BULK);
    }
    await expect.poll(() => capture.receipts.filter(row => row.settled).length).toBe(2);
    await Promise.all(capture.pending);
    expect(capture.receipts[0]).toMatchObject({ status: scenario.status, error: scenario.error, settled: true });
    expect(capture.receipts[1]).toMatchObject({ status: 200, body: { result: { errors: [] } }, settled: true });
    expect(capture.receipts[1].error).toBeUndefined();
    capture.stop();
  });
}
