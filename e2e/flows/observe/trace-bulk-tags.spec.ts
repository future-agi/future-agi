import { request, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedPgTraces } from '../../lib/trace-seed';

// The list the Observe trace grid loads (TraceGrid → readQuery, JSON POST body).
const TRACE_LIST_PATH = '/tracer/trace/list_traces_of_session/';
// The tag write every trace tag editor sends (TraceView.update_tags).
const TAG_PATCH_PATH = /^\/tracer\/trace\/([0-9a-f-]{36})\/tags\/$/;
// Browser-side waits, sized like the other Observe flows for parallel runs.
const UI_READY = 60_000;

const NEW_TAG = 'need improvement';
const KEPT_TAG = 'prod';

interface TraceListRow { trace_id: string; tags?: unknown }

async function openTraceList(page: Page, projectId: string) {
  await page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`,
    { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' });
}

// Primary and compare grids stay mounted; only the visible one is the user's.
const traceRow = (page: Page, traceId: string) =>
  page.locator(`.clean-data-table:visible .ag-row[row-id="${traceId}"]`);

test('OBS-E2E-037: bulk Add tags on selected traces adds the tag to each trace and keeps its existing tags', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-037', area: 'observe',
    userGoal: 'A developer tags several traces at once from the Observe trace list',
    steps: ['send two OTLP traces into one project and register both as Postgres traces (the Django ingestion path), one already tagged `prod`',
            "open the project's trace list and see the existing `prod` tag",
            'select both traces',
            'choose Actions → Add tags and add `need improvement`',
            'see "Tags applied to 2 items"'],
    backendChecks: ['every tag PATCH the page sends carries a list of tag-name strings, one per selected trace',
                    'Postgres tracer_trace.tags keeps `prod` and adds `need improvement` on the tagged trace',
                    'Postgres tracer_trace.tags is exactly [`need improvement`] on the untagged trace'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // CDC (up to POLL.CDC_VISIBLE) + span visibility + several UI waits.
  test.setTimeout(420_000);
  const req = await request.newContext();
  const projectName = `e2e-obs37-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const tagged = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: 'e2e.obs37.tagged',
  });
  const untagged = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: 'e2e.obs37.untagged',
  });
  await req.dispose();

  const projectId = await test.step('storage: both traces visible; project org-scoped', async () => {
    for (const seeded of [tagged, untagged]) {
      await expect.poll(async () => {
        const rows = await probe.ch<{ n: string }>(
          'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
        return Number(rows[0].n);
      }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);
    }
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]);
    expect(projects).toHaveLength(1);
    return projects[0].id;
  });

  await test.step('storage: Postgres trace rows, mirrored to ClickHouse with their tags', async () => {
    seedPgTraces({
      organizationId: actor.organizationId, projectId,
      traces: [
        { id: tagged.traceId, name: 'e2e.obs37.tagged', tags: [KEPT_TAG] },
        { id: untagged.traceId, name: 'e2e.obs37.untagged', tags: [] },
      ],
    });
    // The trace list reads the latest `traces` row's tags (trace_list.py
    // `argMax(tags, _version)`), fed from Postgres by CDC.
    await expect.poll(async () => {
      const rows = await probe.ch<{ tags: string }>(
        'SELECT argMax(tags, _version) AS tags FROM traces WHERE id = {t:UUID} GROUP BY id',
        { t: tagged.traceId });
      return rows[0]?.tags ?? '';
    }, POLL.CDC_VISIBLE).toContain(`"${KEPT_TAG}"`);
  });

  const patches: { traceId: string; body: unknown }[] = [];
  page.on('request', (r) => {
    const match = r.method() === 'PATCH' && new URL(r.url()).pathname.match(TAG_PATCH_PATH);
    if (match) patches.push({ traceId: match[1], body: r.postDataJSON() });
  });

  await test.step('UI: the trace list shows the stored tag', async () => {
    const listResponse = page.waitForResponse((res) =>
      new URL(res.url()).pathname === TRACE_LIST_PATH && res.request().method() === 'POST'
      && res.request().postDataJSON()?.project_id === projectId && res.ok(),
    { timeout: UI_READY });
    await openTraceList(page, projectId);
    const body = await (await listResponse).json();
    const rows: TraceListRow[] = body?.result?.table ?? [];
    // Record the wire shape of `tags` (array vs stored JSON string): the grid
    // has to merge into it either way.
    await testInfo.attach('trace-list-tags', {
      body: JSON.stringify(rows.map((r) => ({ trace_id: r.trace_id, type: typeof r.tags, tags: r.tags })), null, 2),
      contentType: 'application/json',
    });

    await expect(traceRow(page, tagged.traceId).first()).toBeVisible({ timeout: UI_READY });
    await expect(traceRow(page, untagged.traceId).first()).toBeVisible({ timeout: UI_READY });
    await expect(traceRow(page, tagged.traceId).locator('[col-id="tags"]'))
      .toContainText(KEPT_TAG, { timeout: UI_READY });
  });

  await test.step('UI: select both traces and add a tag', async () => {
    for (const traceId of [tagged.traceId, untagged.traceId]) {
      const checkbox = traceRow(page, traceId).locator('.ag-selection-checkbox input[type="checkbox"]');
      await checkbox.click();
      await expect(checkbox).toBeChecked();
    }
    await expect(page.getByText('2 selected', { exact: true })).toBeVisible({ timeout: UI_READY });

    await page.getByRole('button', { name: 'Actions' }).click();
    await page.getByRole('menuitem', { name: 'Add tags' }).click();
    await expect(page.getByText('Add tags to 2 items')).toBeVisible({ timeout: UI_READY });
    await page.getByPlaceholder('Add tag...').fill(NEW_TAG);
    await page.getByPlaceholder('Add tag...').press('Enter');

    await expect(page.getByText('Tags applied to 2 items')).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('Failed to update tags')).toHaveCount(0);
  });

  await test.step('wire: one string-only tag PATCH per selected trace', async () => {
    await expect.poll(() => patches.length, { timeout: UI_READY }).toBe(2);
    await testInfo.attach('tag-patches', { body: JSON.stringify(patches, null, 2), contentType: 'application/json' });
    const byTrace = Object.fromEntries(patches.map((p) => [p.traceId, p.body]));
    expect(byTrace).toEqual({
      [tagged.traceId]: { tags: [KEPT_TAG, NEW_TAG] },
      [untagged.traceId]: { tags: [NEW_TAG] },
    });
  });

  await test.step('storage: Postgres tracer_trace.tags merged per trace', async () => {
    const rows = await probe.pg<{ id: string; tags: string[] }>(
      'SELECT id::text AS id, tags FROM tracer_trace WHERE id = ANY($1::uuid[])',
      [[tagged.traceId, untagged.traceId]]);
    const tags = Object.fromEntries(rows.map((r) => [r.id, r.tags]));
    expect(tags).toEqual({
      [tagged.traceId]: [KEPT_TAG, NEW_TAG],
      [untagged.traceId]: [NEW_TAG],
    });
  });
});
