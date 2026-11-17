import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// Shared anyOf host, not an independently claimable launch owner (launchSurfaces.js).
// This flow proves the history-backed host only, not comment support.
// Selectors: RowPanelShell.jsx and RowActivityTab.jsx at 818b068e64b476b2a694706df2fc84bed6df790e.
type History = { status: boolean; result: { events: {
  id: string; type: string; action: string; actor_id: string; actor: string;
  column_id: string; column: string; before: string | null; after: string | null;
}[]; next_cursor: string | null; has_more: boolean } };

test('@DSX-WALK-COLLAB-02: Reopen a row’s Activity tab and retain its persisted history within the owning tenant.', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'DSX-WALK-COLLAB-02', area: 'dsx-walks',
    userGoal: 'Navigate between Row and Activity and reopen the deep link without losing row identity or leaking history.',
    steps: ['seed a synthetic dataset row in each organization', 'persist a cell edit in tenant A',
      'open Activity through its tab, return to Row, and reopen Activity', 'reload the activity deep link',
      'verify that tenant B cannot read tenant A’s history'],
    backendChecks: ['the history includes the exact persisted cell change and actor',
      'reloading returns the same event ids', 'tenant B receives 404/not_found for A’s activity and 200 for its own'],
  }),
}, async ({ browser, scopeActors }, testInfo) => {
  test.setTimeout(240_000);
  const seeds: { dataset: string; row: string; column: string }[] = [];
  for (const [index, actor] of [scopeActors.ownerA, scopeActors.ownerB].entries()) {
    // ManuallyCreateDataset.jsx submit body, served on the pinned stack.
    const created = await actor.api.post<{ status: boolean; result: {
      dataset_id: string; rows_created: number; columns_created: number;
    } }>('/model-hub/develops/create-dataset-manually/', {
      dataset_name: `${scopeActors.prefix}-activity-host-${index}`, model_type: 'GenerativeLLM',
      number_of_rows: 1, number_of_columns: 1, columns: [{ name: 'input', type: 'text', role: 'input' }],
    });
    expect(created.status).toBe(true);
    expect(created.result).toMatchObject({ rows_created: 1, columns_created: 1 });
    const dataset = created.result.dataset_id;
    const table = await actor.api.get<{ result: { table: { row_id: string }[];
      column_config: { id: string; name: string }[] } }>(`/model-hub/develops/${dataset}/get-dataset-table/`);
    expect(table.result.table).toHaveLength(1);
    expect(table.result.column_config).toHaveLength(1);
    expect(table.result.column_config[0].name).toBe('input');
    seeds.push({ dataset, row: table.result.table[0].row_id, column: table.result.column_config[0].id });
  }
  const [own, other] = seeds;
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    owner: 'dataset.rowTab.activity', hostOnly: true, tenantA: { organization: scopeActors.ownerA.organizationId, ...own },
    tenantB: { organization: scopeActors.ownerB.organizationId, ...other },
  }) });
  for (const value of ['host-before', 'host-after']) {
    expect((await scopeActors.send(scopeActors.ownerA, 'POST', `/model-hub/develops/${own.dataset}/update_cell_value/`, {
      row_id: own.row, column_id: own.column, new_value: value,
    })).status).toBe(200);
  }
  const path = `/model-hub/datasets/${own.dataset}/rows/${own.row}/activity/`;
  const history = await scopeActors.ownerA.api.get<History>(path, { page_size: 50 });
  expect(history.status).toBe(true);
  expect(history.result).toMatchObject({ has_more: false, next_cursor: null });
  expect(history.result.events[0]).toMatchObject({ type: 'edit', action: 'edit', actor_id: scopeActors.ownerA.userId,
    column_id: own.column, column: 'input', before: 'host-before', after: 'host-after' });
  const context = await scopeActors.openContext(browser, scopeActors.ownerA);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['localhost', '127.0.0.1'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
      ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    await page.goto(`/dashboard/develop/${own.dataset}?tab=data&row=${own.row}&rowTab=row`);
    await expect(page.getByTestId('row-tab-row')).toHaveAttribute('aria-selected', 'true');
    await page.getByTestId('row-tab-activity').click();
    await expect(page.getByTestId('row-tab-activity')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('activity-chip-all')).toHaveText(`All ${history.result.events.length}`);
    await expect(page.getByTestId('activity-feed').getByText(`${history.result.events[0].actor} changed input`, { exact: true }))
      .toHaveCount(2);
    await page.getByTestId('row-tab-row').click();
    await expect(page.getByTestId('row-tab-row')).toHaveAttribute('aria-selected', 'true');
    await page.getByTestId('row-tab-activity').click();
    await expect.poll(() => new URL(page.url()).searchParams.get('row')).toBe(own.row);
    await expect.poll(() => new URL(page.url()).searchParams.get('rowTab')).toBe('activity');
    await page.reload();
    await expect(page.getByTestId('row-tab-activity')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('activity-chip-all')).toHaveText(`All ${history.result.events.length}`);
    const reread = await scopeActors.ownerA.api.get<History>(path, { page_size: 50 });
    expect(reread.result.events).toEqual(history.result.events);
    const denied = await scopeActors.send<{ status: boolean; code: string }>(scopeActors.ownerB, 'GET', path);
    expect(denied.status).toBe(404);
    expect(denied.body).toMatchObject({ status: false, code: 'not_found' });
    expect(JSON.stringify(denied.body)).not.toContain('host-after');
    const allowed = await scopeActors.send<History>(scopeActors.ownerB, 'GET',
      `/model-hub/datasets/${other.dataset}/rows/${other.row}/activity/`);
    expect(allowed.status).toBe(200);
    expect(allowed.body.status).toBe(true);
  } finally { await context.close(); }
});
