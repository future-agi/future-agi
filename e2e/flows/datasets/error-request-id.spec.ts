// F76 ON arm: DSX_ERRORS_REQUEST_ID_EXPOSED=true and errors.requestId claimed.
// Historical proof DATA-E2E-003 (j-20261028t0912-38818e) maps to DATA-E2E-176.
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and DatasetCreateEmptyRequestSerializer.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
// useDatasetListQuery.js: datasetListQueryConfig and realDatasetListParams.
const LIST = '/model-hub/develops/get-datasets/';
// ErrorState.jsx and DevelopView.jsx use these native controls.
const COPY_LABEL = 'Copy request id';
const UI_READY = 60_000;
const EXPECTED_ID_SUFFIX = '';
const EXPECTED_FOREIGN_STATUS = 404;
const EXPECTED_NAME_SUFFIX = '';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
type Envelope<T> = { status: boolean; result: T };
type Dataset = { actor: ScopeActor; id: string; name: string };
type List = Envelope<{ datasets: { id: string; name: string }[] }>;

// Only loopback runtime traffic; dependency installation is outside the browser.
const offHost = (url: URL) => /^https?:$/.test(url.protocol)
  && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);

test('DATA-E2E-176: copy the real request id from a cross-origin dataset error and retry', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-176', area: 'datasets',
    userGoal: 'Copy an API error request id for support, then retry without changing my saved datasets',
    steps: [
      'create a uniquely named dataset in each of two synthetic organizations through the public API',
      'open Datasets while a request-method perturbation makes the real list endpoint return 405',
      'copy its request id and paste it into the native Search datasets input',
      'restore GET, clear search and retry to read the original dataset',
      'repeat in the second organization, verify foreign dataset refusals and read back both saved datasets',
    ],
    backendChecks: [
      'the real cross-origin error exposes a unique X-Request-ID that the error-state copy action places on the clipboard exactly',
      'both tenants can read their own saved datasets and each receives 404 when requesting the other tenant dataset evaluations',
      'retry returns each original dataset and API plus Postgres readback retain both exact saved names and tenant identities',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision 120 s + seed/readback 60 s + six UI_READY steps (360 s) + headroom 60 s.
  test.setTimeout(600_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, null, 2),
  });
  const datasets: Dataset[] = [];
  for (const [label, actor] of [['a', scopes.ownerA], ['b', scopes.ownerB]] as const) {
    const name = `e2e-request-id-${label}-${suffix}`;
    const created = await scopes.send<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET,
      { new_dataset_name: name });
    expect(created.status).toBe(200);
    datasets.push({ actor, id: created.body.result.dataset_id, name });
  }
  await attach('seeded-identities', datasets.map(({ actor, ...d }) => ({ ...d,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  const persisted = () => probe.pg<{ id: string; name: string; organization_id: string; workspace_id: string }>(
    'SELECT id, name, organization_id, workspace_id FROM model_hub_dataset WHERE id = ANY($1::uuid[]) ORDER BY id',
    [datasets.map(d => d.id)]);
  const before = await persisted();
  expect(before).toHaveLength(2);
  const seenIds = new Set<string>();

  for (const d of datasets) {
    const context = await scopes.openContext(browser, d.actor);
    const blocked = new Set<string>();
    await context.route(offHost, route => {
      blocked.add(new URL(route.request().url()).host);
      return route.abort();
    });
    let refuse = true;
    // Fault injection changes the request only. Every response is produced by Django.
    await context.route(url => url.pathname === LIST, route => route.continue(
      refuse ? { method: 'PATCH', postData: '{}' } : {}));
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    let requestId = '';
    await test.step(`copy the real error for ${d.name}`, async () => {
      const errors: { id: string; status: number }[] = [];
      page.on('response', response => {
        if (new URL(response.url()).pathname === LIST && response.status() === 405) {
          errors.push({ id: response.headers()['x-request-id'], status: response.status() });
        }
      });
      await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
      const error = page.getByTestId('error-state');
      await ui(error).toBeVisible();
      await ui(error.getByRole('button', { name: COPY_LABEL })).toBeVisible();
      expect(errors.length).toBeGreaterThan(0);
      requestId = errors[errors.length - 1].id;
      expect(requestId).toMatch(UUID);
      expect(seenIds.has(requestId)).toBe(false);
      seenIds.add(requestId);
      // Cross-origin browser fetch explicitly proves JavaScript header readability,
      // rather than treating Playwright's unrestricted wire headers as CORS evidence.
      const crossOrigin = await page.evaluate(async ({ url, headers }) => {
        const response = await fetch(url, { method: 'PATCH', headers, body: '{}' });
        return { status: response.status, id: response.headers.get('X-Request-ID') };
      }, { url: `${E2E.apiUrl}${LIST}`, headers: { ...scopes.headers(d.actor), 'Content-Type': 'application/json' } });
      expect(new URL(E2E.apiUrl).origin).not.toBe(new URL(page.url()).origin);
      expect(crossOrigin.status).toBe(405);
      expect(crossOrigin.id).toMatch(UUID);
      expect(crossOrigin.id).not.toBe(requestId);
      await error.getByRole('button', { name: COPY_LABEL }).click();
      await ui(error.getByRole('button', { name: COPY_LABEL })).toHaveText('Copied');
      // Real OS clipboard, not a navigator.clipboard replacement.
      const search = page.getByRole('textbox', { name: 'Search datasets', exact: true });
      await search.focus();
      await search.press('Control+V');
      await ui(search).toHaveValue(`${requestId}${EXPECTED_ID_SUFFIX}`);
      await attach(`request-ids-${d.id}`, { requestId, crossOrigin });
    });
    await test.step(`retry the original dataset for ${d.name}`, async () => {
      refuse = false;
      await page.getByRole('textbox', { name: 'Search datasets', exact: true }).fill('');
      // Clearing search can itself refetch; Retry is exercised before that debounce.
      const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === LIST
        && response.status() === 200 && response.request().method() === 'GET'
        && !new URL(response.url()).searchParams.has('search_text'), { timeout: UI_READY });
      await page.getByRole('button', { name: /^Retry/ }).click();
      const response = await responsePromise;
      const body = await response.json() as List;
      expect(body.result.datasets.map(row => row.name)).toEqual([`${d.name}${EXPECTED_NAME_SUFFIX}`]);
      await ui(page.getByTestId('error-state')).toHaveCount(0);
      await ui(page.getByText(d.name, { exact: true })).toBeVisible();
    });
    await attach(`off-host-blocked-${d.id}`, [...blocked].sort());
    await context.close();
  }

  await test.step('persisted API data and tenant refusals remain unchanged', async () => {
    for (const d of datasets) {
      const own = await scopes.send<List>(d.actor, 'GET', LIST, undefined, { page: 0, page_size: 25 });
      expect(own.status).toBe(200);
      expect(own.body.result.datasets.map(row => ({ id: row.id, name: row.name })))
        .toEqual([{ id: d.id, name: `${d.name}${EXPECTED_NAME_SUFFIX}` }]);
      // Dataset eval list route, pinned from dataset-eval-version-notice.spec.ts.
      const evals = (id: string) => `/model-hub/develops/${id}/get_evals_list/`;
      const params = { eval_type: 'user' };
      expect((await scopes.send(d.actor, 'GET', evals(d.id), undefined, params)).status).toBe(200);
      const foreign = datasets.find(other => other.id !== d.id)!;
      const denied = await scopes.send(d.actor, 'GET', evals(foreign.id), undefined, params);
      expect(denied.status).toBe(EXPECTED_FOREIGN_STATUS);
      expect(JSON.stringify(denied.body)).not.toContain(foreign.name);
    }
    expect(await persisted()).toEqual(before);
    for (const d of datasets) expect(before.find(row => row.id === d.id)).toEqual({
      id: d.id, name: `${d.name}${EXPECTED_NAME_SUFFIX}`,
      organization_id: d.actor.organizationId, workspace_id: d.actor.workspaceId,
    });
    await attach('persisted-after-retry', await persisted());
  });
});
