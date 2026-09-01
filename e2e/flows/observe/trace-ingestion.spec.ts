import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// The list request the Observe span table issues, pinned off the running app
// (frontend SpanGrid.jsx `buildParams` → endpoints.project.getSpansForObserveProject).
const SPAN_LIST_PATH = '/tracer/observation-span/list_spans_observe/';
const SPAN_PAGE_SIZE = 25;
// Browser-side waits. The local stack slows several-fold when specs run in
// parallel; sized off this flow's whole-test wall time (~8s alone, ~49s beside
// one other spec — first paint was not measured separately), not the 10s
// expect default.
const UI_READY = 60_000;

const ROOT_SPAN = 'e2e.root';
// Fixed by lib/otlp.ts — sendTrace always hangs this LLM child off the root.
const CHILD_SPAN = 'e2e.llm-call';

interface SpanRow { span_id: string; trace_id: string }

test('OBS-E2E-001: SDK trace appears in Observe with coherent backend state', {
  tag: ['@flow', '@smoke'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-001', area: 'observe',
    userGoal: 'A developer sends a trace from their app and inspects it in Observe',
    steps: ['send an OTLP trace with the org API key', 'open Observe project list',
            'open the auto-created project', 'see the root span in the table', 'open the trace detail'],
    backendChecks: ['project row auto-created in PG tracer_project, scoped to the actor org',
                    'both spans present in CH `spans` (FINAL) under that project_id',
                    'curated `traces` row exists for the trace (keyed by `traces.id`)',
                    'the Observe span-list endpoint returns exactly the two seeded spans'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Bounded waits chain to 2x POLL.SPAN_VISIBLE + navigation + 4x UI_READY,
  // well past the config's 120s default; without this a slow run ends as a bare
  // timeout instead of the assertion that actually ran out.
  test.setTimeout(300_000);
  const req = await request.newContext();
  const projectName = `e2e-obs1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: ROOT_SPAN,
  });
  await testInfo.attach('seeded-trace', { body: JSON.stringify(seeded), contentType: 'application/json' });

  const projectId = await test.step('storage: spans, curated trace, org-scoped project', async () => {
    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);

    // `traces` keys the curated row by `id` — there is no trace_id column. The
    // collector writes it as a separate best-effort insert after the span batch
    // (server.go `_ = s.curated.Write(...)` over InsertBestEffort: one POST, no
    // retry), so it can still be in flight once the spans are queryable.
    await expect.poll(async () => {
      const traces = await probe.ch<{ id: string }>(
        'SELECT id FROM traces FINAL WHERE id = {t:UUID}', { t: seeded.traceId });
      return traces.length;
    }, POLL.SPAN_VISIBLE).toBe(1);

    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]);
    expect(projects).toHaveLength(1);

    const spanProjects = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
    expect(spanProjects.map((r) => r.project_id)).toEqual([projects[0].id]);
    return projects[0].id;
  });

  await test.step('UI: project list shows the auto-created project', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(projectName)).toBeVisible({ timeout: UI_READY });
  });

  await test.step('UI: trace table shows the root span; trace detail opens', async () => {
    await page.getByText(projectName).click();
    const traceNames = page.locator('.ag-row [col-id="trace_name"]');
    await expect(traceNames).toHaveText([ROOT_SPAN], { timeout: UI_READY });

    await traceNames.first().click();
    // The drawer renders the selected span's id and the trace tree beneath it.
    await expect(page.getByText(seeded.spanIds[0], { exact: true }).first())
      .toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(CHILD_SPAN).first()).toBeVisible({ timeout: UI_READY });
  });

  await test.step('API: the Observe span-list endpoint returns both seeded spans', async () => {
    const spans = await probe.apiList<SpanRow>(SPAN_LIST_PATH, {
      project_id: projectId, page_number: 0, page_size: SPAN_PAGE_SIZE, filters: '[]',
    });
    expect(spans.map((s) => s.span_id).sort()).toEqual([...seeded.spanIds].sort());
    expect([...new Set(spans.map((s) => s.trace_id))]).toEqual([seeded.traceId]);
  });

  await req.dispose();
});
