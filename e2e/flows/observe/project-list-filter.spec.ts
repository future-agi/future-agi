import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app (frontend src/api/project/observe-project-list.js
// `readObserveProjectPage` → endpoints.project.projectObserveList) and the
// project-list serializer (futureagi/tracer/serializers/project.py
// ProjectListQuerySerializer). The grid's search box maps to `name`; the filter
// panel (Basic + Query tabs) both serialize to `filters`
// (sections/project/common.js buildProjectListApiFilters →
// api/contracts/filter-contract.js buildApiFilterFromPanelRow).
const PROJECT_LIST_PATH = '/tracer/project/list_projects/';
const EMPTY_MESSAGE = 'No projects found'; // ObserveListView.jsx EMPTY_MESSAGE
// Browser-side waits. The local stack slows several-fold under parallel specs;
// sized off this flow's whole-test wall time, not the 10s expect default.
const UI_READY = 60_000;

// A structured filter row as the panel puts it on the wire. Both the Basic tab
// (a field/op/value row) and the Query tab (tokens → rows) reduce to this shape;
// asserting it at the API layer exercises the same query-builder dispatch the
// panel drives. Only `name` and `tags` are filterable on this surface, both
// `text`; the default op is `contains` (common.js:47-54).
const nameFilter = (op: 'contains' | 'equals', value: string) =>
  JSON.stringify([
    { column_id: 'name', filter_config: { filter_type: 'text', filter_op: op, filter_value: value } },
  ]);

interface ProjectRow { id: string; name: string; tags: string[] }

test('OBS-E2E-021: filtering the Observe project list narrows to the chosen project', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-021', area: 'observe',
    userGoal: 'A user narrows the Observe project list to the project they want and trusts the result set',
    steps: ['seed two projects with a shared prefix and distinct names',
            'open the Observe project list',
            'type one project name in the search box',
            'read the filtered list',
            'search a value that matches nothing'],
    backendChecks: ['both seeded projects present in PG tracer_project, scoped to the actor org',
                    'the search box narrows the UI to exactly the matched project (other seeded project gone)',
                    'the list API returns exactly the matched project for the name query, and both for the shared-prefix contains filter',
                    'a value that matches nothing renders the empty-state message and the API returns zero rows'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seeding poll + navigation + several UI_READY steps chain past the 120s
  // default; raise this test's own ceiling so a slow run fails on the assertion
  // that ran out rather than the outer timeout.
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-obs21-${suffix}`;
  const alpha = `${prefix}-alpha`;
  const beta = `${prefix}-beta`;
  const nomatch = `e2e-obs21-none-${suffix}`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };

  await Promise.all([
    sendTrace(req, { ...cfg, projectName: alpha }),
    sendTrace(req, { ...cfg, projectName: beta }),
  ]);
  await testInfo.attach('seeded', {
    body: JSON.stringify({ prefix, alpha, beta }), contentType: 'application/json',
  });

  const { alphaId, betaId } = await test.step('storage: both projects auto-created, org-scoped', async () => {
    // tracer_project rows are created synchronously during ingestion
    // (get_or_create_project); probe.pg reads the primary, so a short poll
    // covers only ingestion latency, not replica lag.
    const rows = await expectPoll(async () => probe.pg<ProjectRow>(
      'SELECT id, name FROM tracer_project WHERE name IN ($1, $2) AND organization_id = $3 AND deleted = false',
      [alpha, beta, actor.organizationId]), (r) => r.length === 2, POLL.SPAN_VISIBLE);
    const byName = Object.fromEntries(rows.map((r) => [r.name, r.id]));
    return { alphaId: byName[alpha], betaId: byName[beta] };
  });

  await test.step('UI: the search box narrows the list to alpha only', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await expect(page.getByPlaceholder('Search')).toBeVisible({ timeout: UI_READY });
    const listed = page.waitForResponse(
      (r) => r.url().includes(PROJECT_LIST_PATH) && r.url().includes(`name=${encodeURIComponent(alpha)}`) && r.ok(),
      { timeout: UI_READY });
    await page.getByPlaceholder('Search').fill(alpha);
    await listed;
    await expect(page.getByText(alpha)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(beta)).toHaveCount(0, { timeout: UI_READY });
  });

  await test.step('API: the list endpoint agrees — name query returns only alpha, prefix contains returns both', async () => {
    const exact = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: alpha, page_number: 0, page_size: 100,
    });
    expect(exact.map((r) => r.id)).toEqual([alphaId]);

    const both = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', page_number: 0, page_size: 100, filters: nameFilter('contains', prefix),
    });
    expect(both.map((r) => r.id).sort()).toEqual([alphaId, betaId].sort());

    const onlyAlpha = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', page_number: 0, page_size: 100, filters: nameFilter('equals', alpha),
    });
    expect(onlyAlpha.map((r) => r.id)).toEqual([alphaId]);
  });

  await test.step('UI + API: a no-match value shows the empty state and returns zero rows', async () => {
    await page.getByPlaceholder('Search').fill(nomatch);
    await expect(page.getByText(EMPTY_MESSAGE)).toBeVisible({ timeout: UI_READY });
    const none = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: nomatch, page_number: 0, page_size: 100,
    });
    expect(none).toHaveLength(0);
  });

  await req.dispose();
});

// Minimal expect.poll wrapper that returns the polled value (Playwright's
// expect.poll asserts but does not hand back the result). Kept local; a shared
// helper would be a lib/ change, which is its own piece of work.
async function expectPoll<T>(
  fn: () => Promise<T>, ok: (v: T) => boolean,
  opts: { timeout: number; intervals?: number[] },
): Promise<T> {
  let last: T;
  await expect.poll(async () => { last = await fn(); return ok(last); }, opts).toBe(true);
  return last!;
}
