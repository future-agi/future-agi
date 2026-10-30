import { request, type Page, type TestInfo } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from tracer/urls.py and serializers/sql_query.py (raw SQL envelopes).
const SQL_RUN = '/tracer/sql-query/run/';
const SQL_RUN_SAVED = '/tracer/sql-query/run-saved/';
const SAVED = '/tracer/saved-sql-query/';
// DashboardCreateUpdateSerializer + DashboardViewSet.create (result envelope).
const DASHBOARDS = '/tracer/dashboard/';
const UI_READY = 60_000;
type Execution = { id: string; project_id: string; state: string; error: string;
  result: { columns: { name: string; type: string }[]; rows: (string | number | null)[][]; truncated: boolean } };

// Fallibility anchors are intentionally separate from seeding and request input.
// Perturb each expected value in an independent run; never change the seed with it.
const EXPECTED_SPAN_COUNT = 2;
const EXPECTED_ROW_COUNT = 1;

test('OBS-E2E-053: an analyst saves a scoped SQL result as a live dashboard query', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-053', area: 'observe',
    userGoal: 'An analyst runs a project-scoped query, shares its definition with project members, and adds it to a dashboard.',
    steps: [
      'send a trace with two independently named spans to a new project',
      'open Observe Query and run a grouped span query using the SQL editor',
      'inspect the exact result table and save a project-shared query',
      'add the saved query to a dashboard using the Query page',
      'open the dashboard and verify it reruns the saved query',
    ],
    backendChecks: [
      'exactly the two seeded spans exist in ClickHouse FINAL under the created project',
      'the created PostgreSQL project belongs to the actor organization and workspace',
      'the completed SQL API result equals the two seeded names and counts shown in the UI',
      'the saved project query and dashboard widget persist the same scoped query reference',
      'the dashboard starts a new personal execution of the saved query and shows the same exact rows',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // SPAN_VISIBLE15 + two ASYNC_JOB60 + five UI_READY60 + 45s navigation/API margin.
  test.setTimeout(480_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-sql-${suffix}`;
  const root = `e2e.sql.root-${suffix}`, child = `e2e.sql.child-${suffix}`;
  const queryName = `e2e-sql-query-${suffix}`, dashboardName = `e2e-sql-dashboard-${suffix}`;
  const sql = 'SELECT name, count() AS n FROM spans GROUP BY name ORDER BY name';
  const expectedRows = [[child, EXPECTED_ROW_COUNT], [root, EXPECTED_ROW_COUNT]];
  try {
    const seeded = await sendTrace(req, { collectorUrl: E2E.collectorUrl,
      apiKey: actor.apiKey, secretKey: actor.secretKey, projectName, rootName: root, childName: child });
    await testInfo.attach('sql-seed', { body: JSON.stringify({ ...seeded, queryName, dashboardName }), contentType: 'application/json' });
    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>('SELECT count() AS n FROM spans FINAL WHERE trace_id = {trace:String}', { trace: seeded.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(EXPECTED_SPAN_COUNT);
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3 AND deleted=false',
      [projectName, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    const spanProjects = await probe.ch<{ project_id: string }>('SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {trace:String}', { trace: seeded.traceId });
    expect(spanProjects.map((value) => value.project_id)).toEqual([projectId]);
    const dashboard = await actor.api.post<{ result: { id: string } }>(DASHBOARDS, { name: dashboardName, description: 'Scoped SQL workbench flow' });
    const dashboardId = dashboard.result.id;
    let jobId = '';
    await test.step('UI: run the query in the project editor', async () => {
      // Route + aria label pinned from SQLQueryView.jsx and dashboard.jsx.
      await page.goto(`/dashboard/observe/${projectId}/query`, { waitUntil: 'domcontentloaded' });
      const editor = page.getByRole('textbox', { name: 'SQL query editor', exact: false });
      await expect(editor).toBeVisible({ timeout: UI_READY });
      // Monaco paints text over its backing textarea; focus it for keyboard input.
      await editor.focus();
      await expect(editor).toBeFocused();
      await editor.press('ControlOrMeta+A');
      await page.keyboard.insertText(sql);
      await attachPage(page, testInfo, 'sql-ui-editor-catalog-and-query');
      const response = page.waitForResponse((res) => res.url().endsWith(SQL_RUN) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Run query', exact: true }).click();
      const submitted = await response;
      expect(submitted.status()).toBe(202);
      jobId = (await submitted.json() as Execution).id;
    }, { timeout: UI_READY });
    let finished: Execution;
    await expect.poll(async () => {
      finished = await actor.api.get<Execution>(`/tracer/sql-query/${jobId}/`, { project_id: projectId });
      return finished.state === 'failed' ? `${finished.state}: ${finished.error}` : finished.state;
    }, POLL.ASYNC_JOB).toBe('succeeded');
    expect(finished!.project_id).toBe(projectId);
    expect(finished!.result.rows).toEqual(expectedRows);
    expect(finished!.result.truncated).toBe(false);
    await test.step('UI: inspect exact rows and save a project-shared query', async () => {
      const cells = page.getByRole('table', { name: 'SQL query results' }).locator('tbody tr');
      await expect(cells).toHaveCount(2, { timeout: UI_READY });
      await expect(cells.nth(0).getByRole('cell')).toHaveText([child, '1'], { timeout: UI_READY });
      await expect(cells.nth(1).getByRole('cell')).toHaveText([root, '1'], { timeout: UI_READY });
      await attachPage(page, testInfo, 'sql-ui-result-table');
      await page.getByRole('button', { name: 'Save query', exact: true }).click();
      await page.getByLabel('Query name', { exact: true }).fill(queryName);
      await page.getByLabel('Visibility', { exact: true }).click();
      await page.getByRole('option', { name: 'Project members', exact: true }).click();
      const savedResponse = page.waitForResponse((res) => res.url().endsWith(SAVED) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
      expect((await savedResponse).status()).toBe(201);
      await expect(page.getByRole('dialog')).toHaveCount(0, { timeout: UI_READY });
      await attachPage(page, testInfo, 'sql-ui-saved-query');
    }, { timeout: UI_READY });
    // Exact table names pinned from tracer/models/sql_query.py and dashboard.py.
    const saved = await probe.pg<{ id: string; sql: string; visibility: string }>(
      'SELECT id, sql, visibility FROM tracer_savedsqlquery WHERE name=$1 AND project_id=$2 AND deleted=false', [queryName, projectId]);
    expect(saved).toHaveLength(1);
    expect(saved[0].sql).toBe(sql);
    expect(saved[0].visibility).toBe('project');
    await test.step('UI: attach the saved reference to the dashboard', async () => {
      await page.getByRole('button', { name: 'Add to dashboard', exact: true }).click();
      await page.getByLabel('Dashboard', { exact: true }).click();
      await page.getByRole('option', { name: dashboardName, exact: true }).click();
      const added = page.waitForResponse((res) => res.url().includes(`/saved-sql-query/${saved[0].id}/add-widget/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Add query', exact: true }).click();
      expect((await added).status()).toBe(201);
    }, { timeout: UI_READY });
    const widgets = await probe.pg<{ id: string; query_config: { source: string; saved_query_id: string; project_id: string } }>(
      'SELECT w.id,w.query_config FROM tracer_dashboardwidget w JOIN tracer_dashboard d ON d.id=w.dashboard_id WHERE d.id=$1 AND d.workspace_id=$2 AND w.name=$3 AND w.deleted=false',
      [dashboardId, actor.workspaceId, queryName]);
    expect(widgets).toHaveLength(1);
    expect(widgets[0].query_config).toEqual({ source: 'sql', saved_query_id: saved[0].id, project_id: projectId });
    await testInfo.attach('sql-identities', { body: JSON.stringify({ projectId, jobId, savedId: saved[0].id, dashboardId, widgetId: widgets[0].id }), contentType: 'application/json' });
    let dashboardJobId = '';
    await test.step('UI: reopen the dashboard and start a fresh saved execution', async () => {
      const rerun = page.waitForResponse((res) => res.url().endsWith(SQL_RUN_SAVED) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.goto(`/dashboard/dashboards/${dashboardId}`, { waitUntil: 'domcontentloaded' });
      const response = await rerun;
      expect(response.status()).toBe(202);
      dashboardJobId = (await response.json() as Execution).id;
      expect(dashboardJobId).not.toBe(jobId);
    }, { timeout: UI_READY });
    await expect.poll(async () => (await actor.api.get<Execution>(`/tracer/sql-query/${dashboardJobId}/`, { project_id: projectId })).state,
      POLL.ASYNC_JOB).toBe('succeeded');
    const rerun = await actor.api.get<Execution>(`/tracer/sql-query/${dashboardJobId}/`, { project_id: projectId });
    expect(rerun.result.rows).toEqual(expectedRows);
    await test.step('UI: dashboard shows the exact rerun rows', async () => {
      const rows = page.getByRole('table', { name: 'SQL query results' }).locator('tbody tr');
      await expect(rows).toHaveCount(2, { timeout: UI_READY });
      await expect(rows.nth(0).getByRole('cell')).toHaveText([child, '1'], { timeout: UI_READY });
      await expect(rows.nth(1).getByRole('cell')).toHaveText([root, '1'], { timeout: UI_READY });
      await attachPage(page, testInfo, 'sql-ui-dashboard-widget');
    }, { timeout: UI_READY });
  } finally {
    await req.dispose();
  }
});

// Additive review evidence only: a full-page PNG per key UI state, no assertions.
async function attachPage(page: Page, testInfo: TestInfo, name: string) {
  await testInfo.attach(name, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
}
