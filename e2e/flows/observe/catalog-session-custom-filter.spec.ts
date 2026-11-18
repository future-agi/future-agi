import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

const LIST = '/tracer/trace-session/list_sessions/';
const VALUES = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000;
type Session = { project_id: string; trace_session_id: string; external_session_id: string };

test.beforeAll(() => {
  // Check before the test's actor fixture can provision any identity.
  for (const endpoint of [E2E.appUrl, E2E.apiUrl, E2E.collectorUrl, E2E.pgUrl, E2E.chUrl]) {
    expect(new URL(endpoint).hostname, 'synthetic local lane only').toBe('localhost');
  }
});

test('OBS-E2E-011: Sessions custom property/value selection preserves project and result scope', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-011', area: 'observe',
    userGoal: 'Select a discovered custom attribute value in Sessions and inspect only its matching session traces',
    steps: ['ingest west and east sessions plus a foreign sibling-project value',
      'choose the custom property and west suggestion through the browser',
      'inspect the exact matching session and open its trace history'],
    backendChecks: ['source sessions retain their exact project identities',
      'the dropdown excludes the sibling value and the request preserves the typed custom predicate',
      'list and detail return only the matching session and its two source traces'],
  }),
}, async ({
  page, request, actor, probe,
}, testInfo) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(UI_READY);
  const prefix = `e2e-session-custom-${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const key = `${prefix}.region`;
  const projectName = `${prefix}-project`;
  const siblingName = `${prefix}-sibling`;
  const seeds = [];
  for (const [project, session, value] of [
    [projectName, `${prefix}-west`, 'west'],
    [projectName, `${prefix}-west`, 'west'],
    [projectName, `${prefix}-east`, 'east'],
    [siblingName, `${prefix}-west`, 'foreign'],
  ]) {
    const attrs = { 'session.id': session, 'user.id': `${prefix}-user`, [key]: value };
    seeds.push({ session, value, ...await sendTrace(request, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: project, rootName: `${prefix}-${seeds.length}`,
      resourceAttributes: { project_type: 'observe' }, rootAttributes: attrs, childAttributes: attrs,
    }) });
  }
  const projects = await probe.pg<{ id: string; name: string }>(
    'SELECT id, name FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND workspace_id = $3',
    [[projectName, siblingName], actor.organizationId, actor.workspaceId]);
  expect(projects.map(p => p.name).sort()).toEqual([projectName, siblingName].sort());
  const projectId = projects.find(p => p.name === projectName)!.id;
  const siblingId = projects.find(p => p.name === siblingName)!.id;
  let sessions: Session[] = [];
  await expect.poll(async () => {
    sessions = await probe.ch<Session>(`SELECT project_id, trace_session_id, external_session_id
      FROM trace_sessions FINAL WHERE project_id IN ({a:UUID}, {b:UUID}) AND is_deleted = 0`,
    { a: projectId, b: siblingId });
    return sessions.map(s => `${s.project_id}:${s.external_session_id}`).sort();
  }, POLL.SPAN_VISIBLE).toEqual([
    `${projectId}:${prefix}-west`, `${projectId}:${prefix}-east`, `${siblingId}:${prefix}-west`,
  ].sort());
  const selectedId = sessions.find(s => s.project_id === projectId && s.external_session_id.endsWith('-west'))!.trace_session_id;
  const allProjectIds = sessions.filter(s => s.project_id === projectId).map(s => s.trace_session_id).sort();
  await expect.poll(async () => {
    const response = await actor.api.post<{ result: { values: { value: unknown; type: string }[] } }>(VALUES, {
      source: 'traces', project_ids: projectId, property_id: `custom_attribute:${key}`, page_size: 25,
    });
    return response.result.values.map(v => ({ value: v.value, type: v.type })).sort((a, b) => String(a.value).localeCompare(String(b.value)));
  }, POLL.ASYNC_JOB).toEqual([{ value: 'east', type: 'string' }, { value: 'west', type: 'string' }]);

  await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
  const visibleIds = () => page.locator('.ag-row [col-id="session_id"]:visible').evaluateAll(cells =>
    cells.map(cell => cell.closest('.ag-row')?.getAttribute('row-id')).sort());
  await expect.poll(visibleIds, { timeout: UI_READY }).toEqual(allProjectIds);
  await page.getByRole('button', { name: 'Filter', exact: true }).click();
  await page.getByRole('button', { name: 'Property', exact: true }).click();
  await page.getByPlaceholder('Search properties...').fill(key);
  await page.locator(`[data-filter-property-option="${key}"][data-filter-property-category="attribute"]`).click();
  const valuesResponse = page.waitForResponse(r => r.url().includes(VALUES)
    && r.request().method() === 'POST' && Boolean(r.request().postData()?.includes(key)), { timeout: UI_READY });
  await page.locator(`[data-filter-value-trigger="${key}"]`).click();
  const choices = await valuesResponse;
  expect(choices.status()).toBe(200);
  expect(choices.request().postDataJSON()).toMatchObject({
    source: 'traces', project_ids: projectId, property_id: `custom_attribute:${key}`,
  });
  const choicesBody = await choices.json();
  expect(choicesBody.result.values.map((v: { value: string }) => v.value).sort()).toEqual(['east', 'west']);
  await expect(page.locator('[data-filter-value-option="foreign"]')).toHaveCount(0);
  const filtered = page.waitForResponse(r => {
    if (!r.url().includes(LIST) || r.request().method() === 'OPTIONS') return false;
    const wire = r.request().method() === 'POST' ? r.request().postDataJSON()
      : Object.fromEntries(new URL(r.url()).searchParams);
    const filters = typeof wire.filters === 'string' ? JSON.parse(wire.filters) : wire.filters;
    return Array.isArray(filters) && filters.some(f => f.column_id === key);
  }, { timeout: UI_READY });
  await page.locator('[data-filter-value-option="west"][role="checkbox"]').click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  const result = await filtered;
  expect(result.status()).toBe(200);
  const wire = result.request().method() === 'POST' ? result.request().postDataJSON()
    : Object.fromEntries(new URL(result.url()).searchParams);
  const filters = typeof wire.filters === 'string' ? JSON.parse(wire.filters) : wire.filters;
  const filter = filters.find((f: { column_id: string }) => f.column_id === key);
  expect(filter).toMatchObject({ column_id: key, property_id: `custom_attribute:${key}`,
    filter_config: { filter_type: 'text', filter_op: 'in', filter_value: ['west'], attribute_value_types: ['string'] } });
  const body = await result.json();
  expect(wire.project_id).toBe(projectId);
  expect(body.result.metadata).toMatchObject({ query_complete: true, query_status: 'complete',
    total_rows: 1, total_rows_exact: 1, total_rows_is_lower_bound: false, has_more: false,
    query_applied_filter_count: 1 });
  expect(body.result.table.map((s: { session_id: string }) => s.session_id)).toEqual([selectedId]);
  expect(body.result.table[0].total_traces_count).toBe(2);
  await expect.poll(visibleIds, { timeout: UI_READY }).toEqual([selectedId]);
  await testInfo.attach('custom-session-filter', { contentType: 'application/json',
    body: JSON.stringify({ projects, sessions, sourceTraces: seeds, choices: choicesBody, request: wire, response: body }) });

  const detailResponse = page.waitForResponse(r => r.url().includes(`/tracer/trace-session/${selectedId}/query/`)
    && r.request().method() !== 'OPTIONS', { timeout: UI_READY });
  await page.locator('.ag-row [col-id="session_id"]:visible').click();
  const detail = await detailResponse;
  expect(detail.status()).toBe(200);
  const detailBody = await detail.json();
  expect(detailBody.result.response.map((r: { trace_id: string }) => r.trace_id).sort())
    .toEqual(seeds.slice(0, 2).map(s => s.traceId).sort());
  await expect(page.getByRole('tab', { name: 'Session History', exact: true })).toBeVisible();
  await testInfo.attach('custom-session-detail', { contentType: 'application/json', body: JSON.stringify(detailBody) });
});
