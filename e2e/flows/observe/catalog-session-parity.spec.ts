import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Session-grid.buildParams, TracesDrawer.queryFn and utils/axios.js.
const LIST = '/tracer/trace-session/list_sessions/';
const LEGACY_VALUES = '/tracer/trace-session/get_session_filter_values/';
const VALUES = '/tracer/dashboard/filter_values/';
// TraceFilterPanel.filterValueAdapterMetricName maps session_id to session.
const SESSION_PROPERTY = 'system_attribute:sessions:session';
const UI_READY = 60_000;
interface Session { project_id: string; trace_session_id: string; external_session_id: string }
interface SessionRow { session_id: string; session_name: string | null; total_traces_count: number }
interface Value { value: string; label: string }
interface ValuePage { result: { values: Value[]; next_cursor: string | null; has_more: boolean } }

test('OBS-E2E-006: session suggestions select only their own traces', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-006', area: 'observe',
    userGoal: 'Find a session through discovered values and inspect only its traces',
    steps: ['ingest two traces in one session, a different session and a same-named sibling-project session',
      'read scoped session suggestions and paginated values', 'select a session in the Sessions filter',
      'open its detail and inspect the two trace identities', 'open one trace from the session'],
    backendChecks: ['curated session identities and all source spans retain their exact project grouping',
      'native and catalog value reads return only the requested project sessions, including pagination and custom values',
      'the UI filter, session list and detail return exactly the selected session and its two traces'],
  }),
}, async ({ page, request, actor, probe }, testInfo) => {
  // SPAN_VISIBLE 15 + ASYNC_JOB 60 + five UI_READY 300 + 45s headroom.
  test.setTimeout(420_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-obs6-${suffix}`;
  const projectName = `${prefix}-project`;
  const siblingName = `${prefix}-sibling`;
  const shared = `${prefix}-shared`;
  const other = `${prefix}-other`;
  // converter.go:newSpanIdentity pins session.id/user.id and requires the
  // observe resource hint for user identity. Both spans carry the grouping.
  const seeds = [];
  for (const [name, session, region] of [
    [projectName, shared, 'west'], [projectName, shared, 'west'],
    [projectName, other, 'east'], [siblingName, shared, 'foreign'],
  ]) {
    const attributes = { 'session.id': session, 'user.id': `${prefix}-user`, region };
    seeds.push({ session, region, ...(await sendTrace(request, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: name, rootName: `${prefix}-trace-${seeds.length}`,
      resourceAttributes: { project_type: 'observe' }, rootAttributes: attributes,
      childAttributes: attributes,
    })) });
  }
  await testInfo.attach('seeded-sessions', { body: JSON.stringify(seeds), contentType: 'application/json' });
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
  }, POLL.SPAN_VISIBLE).toEqual([`${projectId}:${shared}`, `${projectId}:${other}`, `${siblingId}:${shared}`].sort());
  expect(new Set(sessions.map(s => s.trace_session_id)).size).toBe(3);
  const expectedSpans = seeds.flatMap(seed => seed.spanIds.map(id => ({
    id, trace_id: seed.traceId,
    project_id: seed.projectName === projectName ? projectId : siblingId,
    trace_session_id: sessions.find(s => s.external_session_id === seed.session
      && s.project_id === (seed.projectName === projectName ? projectId : siblingId))!.trace_session_id,
  }))).sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(() => probe.ch(`SELECT id, trace_id, project_id, trace_session_id
    FROM spans FINAL WHERE project_id IN ({a:UUID}, {b:UUID}) ORDER BY id`,
  { a: projectId, b: siblingId }), POLL.SPAN_VISIBLE).toEqual(expectedSpans);
  await testInfo.attach('session-identities', { body: JSON.stringify({ projects, sessions, expectedSpans }),
    contentType: 'application/json' });

  const expectedValues = sessions.filter(s => s.project_id === projectId)
    .map(s => ({ value: s.trace_session_id, label: s.external_session_id }))
    .sort((a, b) => a.label.localeCompare(b.label));
  await expect.poll(async () => {
    const [native, custom] = await Promise.all([
      actor.api.get<{ result: { values: Value[] } }>(LEGACY_VALUES,
        { project_id: projectId, column: 'session_id', page_size: 25 }),
      // Sessions custom-value requests deliberately use traces transport.
      actor.api.post<ValuePage>(VALUES, { source: 'traces', project_ids: projectId,
        property_id: 'custom_attribute:region', page_size: 25 }),
    ]);
    return { sessions: native.result.values, regions: custom.result.values.map(v => v.value).sort() };
  }, POLL.ASYNC_JOB).toEqual({ sessions: expectedValues, regions: ['east', 'west'] });
  const valueParams = { property_id: SESSION_PROPERTY, source: 'sessions', project_ids: projectId, page_size: 1 };
  const first = await actor.api.post<ValuePage>(VALUES, valueParams);
  expect(first.result.values).toHaveLength(1);
  expect(first.result.has_more).toBe(true);
  expect(first.result.next_cursor).toEqual(expect.any(String));
  const second = await actor.api.post<ValuePage>(VALUES, { ...valueParams, cursor: first.result.next_cursor });
  expect(second.result.values).toHaveLength(1);
  // Cursor pages use catalog identity order, not the legacy endpoint's label
  // order. Assert the complete union, so omission or duplication cannot pass.
  expect([...first.result.values, ...second.result.values]
    .map(v => ({ value: v.value, label: v.label })).sort((a, b) => a.label.localeCompare(b.label)))
    .toEqual(expectedValues);
  const repeated = await actor.api.post<ValuePage>(VALUES, valueParams);
  expect(repeated.result.values).toEqual(first.result.values);
  expect(second.result.has_more).toBe(false);
  const siblingValues = await actor.api.post<ValuePage>(VALUES, { ...valueParams, project_ids: siblingId });
  expect(siblingValues.result.values.map(v => ({ value: v.value, label: v.label })))
    .toEqual(sessions.filter(s => s.project_id === siblingId).map(s => ({ value: s.trace_session_id, label: shared })));
  // LLMTracingView routes the Sessions tab to this dedicated page.
  await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
  const sessionCells = page.locator('.ag-row [col-id="session_id"]:visible');
  // Session-grid.getRowId uses the immutable session_id. Display labels come
  // from a separate 60–120s dictionary cache and may initially be null; the
  // SessionCellRenderer intentionally falls back to the UUID in that case.
  const visibleSessionIds = () => sessionCells.evaluateAll(cells =>
    cells.map(cell => cell.closest('.ag-row')?.getAttribute('row-id')).sort());
  await expect.poll(visibleSessionIds, { timeout: UI_READY })
    .toEqual(expectedValues.map(v => v.value).sort());
  await page.getByRole('button', { name: 'Filter', exact: true }).click();
  await page.getByRole('button', { name: 'Property', exact: true }).click();
  await page.getByPlaceholder('Search properties...').fill('Session ID');
  await page.locator('[data-filter-property-option="session_id"]').click();
  await page.locator('[data-filter-value-trigger="session_id"]').click();
  const selectedId = expectedValues.find(v => v.label === shared)!.value;
  const filtered = page.waitForResponse(r => {
    if (!r.url().includes(LIST) || !r.ok() || r.request().method() === 'OPTIONS') return false;
    const params = r.request().method() === 'POST' ? r.request().postDataJSON()
      : Object.fromEntries(new URL(r.url()).searchParams);
    return JSON.parse(params.filters).some((f: { column_id: string; filter_config: { filter_value: unknown } }) =>
      f.column_id === 'session_id' && JSON.stringify(f.filter_config.filter_value) === JSON.stringify([selectedId]));
  }, { timeout: UI_READY });
  await page.getByPlaceholder('Search values...').fill(shared);
  await page.locator(`[data-filter-value-option="${selectedId}"][role="checkbox"]`).click();
  await page.keyboard.press('Escape');
  await expect(page.getByPlaceholder('Search values...')).toBeHidden({ timeout: UI_READY });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
  const listResponse = await filtered;
  expect(listResponse.status()).toBe(200);
  const list = await listResponse.json();
  expect(list.result.table.map((r: SessionRow) => r.session_id)).toEqual([selectedId]);
  expect(list.result.table.map((r: SessionRow) => r.total_traces_count)).toEqual([2]);
  await expect.poll(visibleSessionIds, { timeout: UI_READY }).toEqual([selectedId]);
  expect([shared, null]).toContain(list.result.table[0].session_name);
  await expect(sessionCells).toHaveText([list.result.table[0].session_name ?? selectedId], { timeout: UI_READY });
  const detailResponse = page.waitForResponse(r => r.url().includes(`/tracer/trace-session/${selectedId}/query/`)
    && r.request().method() !== 'OPTIONS' && r.ok(), { timeout: UI_READY });
  await sessionCells.click();
  const detail = await (await detailResponse).json();
  const expectedTraceIds = seeds.slice(0, 2).map(s => s.traceId).sort();
  expect(detail.result.response.map((r: { trace_id: string }) => r.trace_id).sort()).toEqual(expectedTraceIds);
  await expect(page.getByRole('tab', { name: 'Session History', exact: true })).toBeVisible({ timeout: UI_READY });
  // Closed trace-detail drawers remain mounted alongside the session drawer.
  const sessionDrawer = page.locator('.MuiDrawer-paper:visible').filter({
    has: page.getByRole('tablist', { name: 'session drawer tabs' }),
  });
  for (const id of expectedTraceIds) await expect(sessionDrawer.getByText(id, { exact: true })
    .filter({ visible: true })).toBeVisible({ timeout: UI_READY });
  for (const seed of seeds.slice(2)) await expect(sessionDrawer.getByText(seed.traceId, { exact: true })).toHaveCount(0);
  await testInfo.attach('filtered-session-and-traces', { body: JSON.stringify({ list, detail }), contentType: 'application/json' });
  // Each card contains its exact trace ID and its own View Trace button.
  const firstTrace = detail.result.response[0].trace_id;
  await sessionDrawer.getByRole('button', { name: 'View Trace', exact: true }).first().click();
  const opened = seeds.find(s => s.traceId === firstTrace)!;
  await expect(page.getByText(opened.spanIds[0], { exact: true }).first()).toBeVisible({ timeout: UI_READY });
});
