import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// DatasetStarToggle.jsx, useDatasetStarSupport.js and DatasetStarView.
// Companion list.organize.star.md used; id 55 avoids the other writers' ORG range.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const LIST = '/model-hub/develops/get-datasets/';
const star = (id: string) => `/model-hub/datasets/${id}/star/`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Listed = { result: { datasets: { id: string; name: string; starred: boolean }[]; total_count: number } };

test('@DSX-WALK-ORG-55: Persist a personal dataset star without changing another user’s preference or exposing another tenant.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-ORG-55', area: 'dsx-walks',
    userGoal: 'Star a dataset, find it first after reload, and unstar it without affecting another user or tenant',
    steps: ['seed two datasets in tenant A and one in B and require the live stars capability',
      'star the older A dataset in the list, reload and inspect Starred',
      'check a same-workspace viewer’s independent preference and reject tenant B writes',
      'unstar in the row menu and reload the empty Starred list'],
    backendChecks: ['POST and DELETE return exact dataset_id/starred fields; storage contains one unique personal star row',
      'list ordering and the Starred filter match exact seeded ids and persist across reload',
      'viewer preferences are independent; foreign POST/DELETE return 404 and foreign list search is empty'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA, b = scopes.ownerB, viewer = scopes.viewer;
  const stem = `e2e-dsx-org55-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seeds: { id: string; name: string }[] = [];
  for (const [actor, suffix] of [[a, 'alpha'], [a, 'zulu'], [b, 'foreign']] as const) {
    const name = `${stem}-${suffix}`;
    // Exact ManuallyCreateDataset.submit shape, with one plain synthetic input column.
    const created = await scopes.send<Created>(actor, 'POST', CREATE, {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1,
      columns: [{ name: 'input', type: 'text', role: 'input' }],
    });
    expect(created.status).toBe(200);
    seeds.push({ id: created.body.result.dataset_id, name });
  }
  const [alpha, zulu, foreign] = seeds;
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    seeds, organizationA: a.organizationId, organizationB: b.organizationId, owner: a.userId, viewer: viewer.userId,
  }) });
  const capability = await a.api.get<{ features: { dataset_stars: { allowed: boolean } } }>('/api/capabilities/');
  expect(capability.features.dataset_stars.allowed, 'A denial is not a passing persistence walk').toBe(true);
  const stored = () => probe.pg<{ dataset_id: string; user_id: string }>(
    'SELECT dataset_id, user_id FROM model_hub_datasetstar WHERE dataset_id=$1 ORDER BY user_id', [alpha.id]);
  const list = (actor: typeof a, starred = false) => actor.api.get<Listed>(LIST, {
    search_text: stem, page: 0, page_size: 25, starred: String(starred),
    sort: JSON.stringify([{ column_id: 'created_at', type: 'descending' }]),
  });
  expect((await list(a)).result.datasets.map(r => r.id)).toEqual([zulu.id, alpha.id]);
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', r => ['localhost', '127.0.0.1'].includes(new URL(r.request().url()).hostname)
    ? r.continue() : r.abort('blockedbyclient'));
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto(`/dashboard/develop?q=${encodeURIComponent(stem)}`, { waitUntil: 'domcontentloaded' });
    const toggle = page.getByRole('button', { name: `Star ${alpha.name}`, exact: true });
    await expect(toggle).toBeEnabled();
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');
    const posted = page.waitForResponse(r => new URL(r.url()).pathname === star(alpha.id) && r.request().method() === 'POST');
    await toggle.click();
    const added = await posted;
    expect(added.status()).toBe(200);
    expect(await added.json()).toEqual({ status: true, result: { dataset_id: alpha.id, starred: true } });
    expect(added.request().postData()).toBeNull();
    expect(new URL(page.url()).pathname).toBe('/dashboard/develop');
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(await stored()).toEqual([{ dataset_id: alpha.id, user_id: a.userId }]);
    expect((await scopes.send(a, 'POST', star(alpha.id))).status).toBe(200);
    expect(await stored()).toEqual([{ dataset_id: alpha.id, user_id: a.userId }]);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(toggle).toHaveAttribute('data-starred', 'true');
    const names = page.getByRole('grid', { name: 'Datasets', exact: true }).locator('.MuiDataGrid-row [data-field="name"] a');
    await expect(names).toHaveText([alpha.name, zulu.name]);
    expect((await list(a)).result.datasets.map(({ id, starred }) => ({ id, starred }))).toEqual([
      { id: alpha.id, starred: true }, { id: zulu.id, starred: false },
    ]);
    const filter = page.getByRole('navigation', { name: 'Folders', exact: true }).getByRole('button', { name: /^Starred\s*1$/ });
    await filter.click();
    await expect(names).toHaveText([alpha.name]);
    expect((await list(a, true)).result).toMatchObject({ total_count: 1, datasets: [expect.objectContaining({ id: alpha.id, starred: true })] });
    expect((await list(viewer, true)).result.datasets).toEqual([]);
    expect((await scopes.send(viewer, 'POST', star(alpha.id))).status).toBe(200);
    expect((await scopes.send(viewer, 'DELETE', star(alpha.id))).status).toBe(200);
    expect((await list(viewer, true)).result.datasets).toEqual([]);
    expect(await stored()).toEqual([{ dataset_id: alpha.id, user_id: a.userId }]);
    expect((await scopes.send(b, 'POST', star(foreign.id))).status).toBe(200);
    for (const method of ['POST', 'DELETE']) {
      expect((await scopes.send(b, method, star(alpha.id))).status).toBe(404);
      expect((await scopes.send(a, method, star(foreign.id))).status).toBe(404);
    }
    const denied = await b.api.get<Listed>(LIST, { search_text: alpha.name });
    expect(denied.result).toMatchObject({ total_count: 0, datasets: [] });
    expect(await stored()).toEqual([{ dataset_id: alpha.id, user_id: a.userId }]);
    await page.getByRole('button', { name: `${alpha.name} actions`, exact: true }).click();
    const deleting = page.waitForResponse(r => new URL(r.url()).pathname === star(alpha.id) && r.request().method() === 'DELETE');
    await page.getByRole('menuitem', { name: 'Unstar', exact: true }).click();
    const removed = await deleting;
    expect(removed.status()).toBe(200);
    expect(await removed.json()).toEqual({ status: true, result: { dataset_id: alpha.id, starred: false } });
    await expect(page.getByText('No starred datasets match', { exact: true })).toBeVisible();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByText('No starred datasets match', { exact: true })).toBeVisible();
    expect((await list(a, true)).result).toMatchObject({ total_count: 0, datasets: [] });
    expect(await stored()).toEqual([]);
    expect((await list(b, true)).result.datasets.map(r => r.id)).toEqual([foreign.id]);
  } finally { await context.close(); }
});
