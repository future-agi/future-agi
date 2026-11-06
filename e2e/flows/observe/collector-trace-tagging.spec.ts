import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// TraceGrid.jsx buildParams and utils/axios.js: the curated trace-list read.
const TRACE_LIST_PATH = '/tracer/trace/list_traces_of_session/';
const TRACE_PAGE_SIZE = 25;
// e2e/README.md: one browser action / short shared-load chain's first-paint budget.
const UI_READY = 60_000;
// TagInput.jsx submits { name, color }; project_tags.py validates #RRGGBB colours.
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

type TraceRow = { trace_id: string; tags: string[] };
type TraceList = { result: { table: TraceRow[] } };
type TagWrite = { tags: Array<{ name: string; color: string }> };
// tracer/serializers/project_tags.py: ProjectTagDefinitionListResponseSerializer.
type TagDefinitions = {
  status: boolean;
  result: { tags: Array<{ name: string; color: string; usage_count: number }> };
};

test('OBS-E2E-059: a collector-ingested trace can be tagged from the Observe grid', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-059', area: 'observe',
    userGoal: "A user tags a collector-ingested trace from the Observe grid, sees the tag on its row, and finds it in the project's tag list",
    steps: [
      'ingest one trace through the collector into a uniquely named project and wait until it is listed',
      'open the project trace grid and use Display to show Trace Name and Tags',
      'open Add tags on the seeded row, enter a unique tag and press Enter',
      'see Tags updated, close the popover and read the tag on the refreshed row',
      'read the project tag definitions through the authenticated API',
    ],
    backendChecks: [
      'PATCH /tracer/trace/<traceId>/tags/ accepts an object tag without project_id for a trace absent from Postgres and returns its assigned name',
      'the refreshed trace-list response and ClickHouse traces FINAL contain exactly the new tag on the seeded trace',
      'GET /tracer/project/<projectId>/tag-definitions/ lists the unique tag with its submitted #RRGGBB colour and usage_count >= 1',
    ],
    browserChecks: [
      'saving the tag shows the Tags updated snackbar',
      'closing the popover refreshes the grid and the seeded row displays exactly the tag name',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // 3 * SPAN_VISIBLE (45s: spans, curated trace, list) + 5 * UI_READY
  // (300s: open, columns, save, refresh, backend reads) + 75s seeding/headroom.
  test.setTimeout(420_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  try {
    // The standard actor/org is fresh per worker; names isolate this flow from
    // other specs sharing that actor. Never seed a Postgres trace or write SQL.
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-obs59-${suffix}`;
    const rootName = `e2e-root-${suffix}`;
    const tagName = `e2e-tag-${suffix}`;
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
      secretKey: actor.secretKey, projectName, rootName,
    });
    await testInfo.attach('seeded-trace', {
      body: JSON.stringify({ ...seeded, rootName, tagName,
        organizationId: actor.organizationId, workspaceId: actor.workspaceId }),
      contentType: 'application/json',
    });

    const projectId = await test.step('precondition: collector-only trace is listed', async () => {
      await expect.poll(async () => {
        const spans = await probe.ch<{ id: string }>(
          'SELECT id FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id',
          { t: seeded.traceId });
        return spans.map(span => span.id);
      }, POLL.SPAN_VISIBLE).toEqual([...seeded.spanIds].sort());

      // tracer/models/project.py: the collector auto-creates the scoped project.
      const projects = await probe.pg<{ id: string }>(
        'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND workspace_id = $3',
        [projectName, actor.organizationId, actor.workspaceId]);
      expect(projects).toHaveLength(1);
      const id = projects[0].id;
      await testInfo.attach('seeded-project', {
        body: JSON.stringify({ projectId: id, projectName }), contentType: 'application/json',
      });

      // fi-collector/pkg/server/server.go: curated.Write is best-effort AFTER
      // the span batch. Give traces.id (UUID, not spans.trace_id String) its own wait.
      await expect.poll(() => probe.ch<{ id: string; project_id: string }>(
        'SELECT id, project_id FROM traces FINAL WHERE id = {t:UUID}',
        { t: seeded.traceId }), POLL.SPAN_VISIBLE)
        .toEqual([{ id: seeded.traceId, project_id: id }]);
      // tracer/models/trace.py: prove the regression's ClickHouse-only precondition.
      expect(await probe.pg('SELECT id FROM tracer_trace WHERE id = $1', [seeded.traceId]))
        .toEqual([]);
      await expect.poll(async () => {
        const rows = await probe.apiList<TraceRow>(TRACE_LIST_PATH, {
          project_id: id, page_number: 0, page_size: TRACE_PAGE_SIZE, filters: '[]',
        });
        return rows.map(row => row.trace_id);
      }, POLL.SPAN_VISIBLE).toEqual([seeded.traceId]);
      return id;
    });

    // TraceGrid.jsx/getTraceGridRowId: project-pinned rows use the bare trace UUID.
    // Primary/compare grids stay mounted; only interact with the visible one.
    const row = page.locator(
      `.ag-root-wrapper:visible .ag-center-cols-container .ag-row[row-id="${seeded.traceId}"]`,
    );
    const tagsCell = row.locator('[col-id="tags"]');
    await test.step('UI: open the ingested trace in Observe', async () => {
      // LLMTracingView.jsx: trace grouping, primary graph and the Traces tab.
      await page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace&selectedGraph=primary&tab=traces`,
        { waitUntil: 'domcontentloaded' });
      await expect(row.locator('[col-id="trace_name"]')).toHaveText(rootName, { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('UI: make the Tags column visible', async () => {
      // DisplayPanel.jsx -> project-detail/ColumnDropdown/ColumnConfigureDropDown.jsx.
      // Keep only identity and Tags so horizontal virtualization cannot hide the
      // tags cell even when the column was already enabled in the default view.
      await page.getByRole('button', { name: 'Display', exact: true }).click();
      await page.getByText('View columns', { exact: true }).click();
      const columns = page.locator('.MuiPopover-paper:visible').filter({
        has: page.getByPlaceholder('Search', { exact: true }),
      });
      const selectAll = columns.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
      await selectAll.check();
      await selectAll.uncheck();
      await columns.getByText('Trace Name', { exact: true }).locator('..').getByRole('checkbox').check();
      await columns.getByPlaceholder('Search', { exact: true }).fill('Tags');
      await columns.getByText('Tags', { exact: true }).locator('..').getByRole('checkbox').check();
      await page.keyboard.press('Escape');
      await expect(columns).toBeHidden({ timeout: UI_READY });
      await page.keyboard.press('Escape');
      await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
      // Renderers/TagsCell.jsx supplies this accessible name for an empty tags cell.
      await expect(tagsCell.getByRole('button', { name: 'Add tags', exact: true }))
        .toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    const submittedColor = await test.step('UI/API: save an object tag without project_id', async () => {
      await tagsCell.getByRole('button', { name: 'Add tags', exact: true }).click();
      // AddTagsPopover.jsx + TagInput.jsx: Enter persists, but leaves the popover open.
      const input = page.getByPlaceholder('Add tag...', { exact: true });
      await input.fill(tagName);
      const tagPath = `/tracer/trace/${seeded.traceId}/tags/`;
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === tagPath
          && r.request().method() === 'PATCH', { timeout: UI_READY }),
        input.press('Enter'),
      ]);
      const outgoing = response.request();
      const sent = outgoing.postDataJSON() as TagWrite;
      const body = await response.json();
      await testInfo.attach('tag-write', {
        body: JSON.stringify({ url: outgoing.url(), request: sent, status: response.status(), response: body }),
        contentType: 'application/json',
      });
      // Do not filter the waiter by r.ok(): the pre-fix 400 must fail here,
      // with its actual response, rather than masquerading as a wait timeout.
      expect(response.status(), JSON.stringify(body)).toBe(200);
      expect(new URL(outgoing.url()).searchParams.has('project_id')).toBe(false);
      expect(sent).toEqual({ tags: [{ name: tagName, color: expect.stringMatching(HEX_COLOR) }] });
      expect(body).toMatchObject({ status: true, result: { id: seeded.traceId, tags: [tagName] } });
      await expect(page.getByText('Tags updated', { exact: true })).toBeVisible({ timeout: UI_READY });
      return sent.tags[0].color;
    }, { timeout: UI_READY });

    await test.step('UI/API: closing the popover reloads the tagged trace row', async () => {
      // TagsCell.handleClose -> CustomTraceRenderer.onTagsUpdated -> refreshServerSide.
      // Arm before Escape, and require the real list response, not the popover's
      // optimistic chip. TraceGrid/readQuery supports GET and POST list reads.
      const [response] = await Promise.all([
        page.waitForResponse(r => {
          if (new URL(r.url()).pathname !== TRACE_LIST_PATH
            || !['GET', 'POST'].includes(r.request().method())) return false;
          const params = r.request().method() === 'POST'
            ? r.request().postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams);
          return params.project_id === projectId;
        }, { timeout: UI_READY }),
        page.keyboard.press('Escape'),
      ]);
      expect(response.status()).toBe(200);
      const body = await response.json() as TraceList;
      await testInfo.attach('refreshed-trace-list', {
        body: JSON.stringify(body), contentType: 'application/json',
      });
      expect(body.result.table.map(trace => ({ trace_id: trace.trace_id, tags: trace.tags })))
        .toEqual([{ trace_id: seeded.traceId, tags: [tagName] }]);
      await expect(page.getByPlaceholder('Add tag...', { exact: true })).toBeHidden({ timeout: UI_READY });
      await expect(tagsCell.getByRole('button', { name: 'Edit tags', exact: true }))
        .toHaveText(tagName, { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('storage/API: the trace tag is persisted and listed with colour and usage', async () => {
      // project_tags.py writes traces.tags as JSON text, synchronously before PATCH returns.
      expect(await probe.ch<{ id: string; project_id: string; tags: string[] }>(
        "SELECT id, project_id, JSONExtract(tags, 'Array(String)') AS tags FROM traces FINAL WHERE id = {t:UUID}",
        { t: seeded.traceId }))
        .toEqual([{ id: seeded.traceId, project_id: projectId, tags: [tagName] }]);
      // ApiClient throws on the pre-fix 404. apiList is NOT suitable: this
      // endpoint's envelope is result.tags, not result.table (project_tags.py).
      const definitions = await actor.api.get<TagDefinitions>(`/tracer/project/${projectId}/tag-definitions/`);
      await testInfo.attach('project-tag-definitions', {
        body: JSON.stringify(definitions), contentType: 'application/json',
      });
      expect(definitions.status).toBe(true);
      expect(definitions.result.tags).toEqual([
        { name: tagName, color: submittedColor, usage_count: expect.any(Number) },
      ]);
      expect(definitions.result.tags[0].color).toMatch(HEX_COLOR);
      expect(definitions.result.tags[0].usage_count).toBeGreaterThanOrEqual(1);
    }, { timeout: UI_READY });
  } finally {
    await req.dispose();
  }
});
