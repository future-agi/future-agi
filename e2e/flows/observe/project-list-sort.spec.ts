import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app: the project list request
// (observe-project-list.js) and its server-side sort params
// (ObserveListView.jsx sends sort_by/sort_direction; sortingMode="server").
// Only the "Project" (name) column is header-sortable; the backend
// (project.py ALLOWED_SORT_FIELDS) allows name/created_at/updated_at and 400s
// on a bad sort_direction.
const PROJECT_LIST_PATH = '/tracer/project/list_projects/';
const UI_READY = 60_000;

interface ProjectRow { id: string; name: string }

test('OBS-E2E-023: sorting the project list by name holds in the UI and the API', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-023', area: 'observe',
    userGoal: 'A user sorts the Observe list by project name and the order holds end-to-end',
    steps: ['seed three projects whose names sort a < b < c',
            'open the list and filter to their shared prefix',
            'sort ascending by the Project column',
            'sort descending',
            'read the order in the UI and from the list API'],
    backendChecks: ['all three seeded projects present in PG, org-scoped',
                    'ascending: the three rows render top-to-bottom a, b, c; the list API returns the same order',
                    'descending reverses the order and drops no rows'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-obs23-${suffix}`;
  // Names sort a < b < c lexicographically within the prefix.
  const a = `${prefix}-a`;
  const b = `${prefix}-b`;
  const c = `${prefix}-c`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };

  await Promise.all([
    sendTrace(req, { ...cfg, projectName: a }),
    sendTrace(req, { ...cfg, projectName: b }),
    sendTrace(req, { ...cfg, projectName: c }),
  ]);
  await testInfo.attach('seeded', { body: JSON.stringify({ prefix, a, b, c }), contentType: 'application/json' });

  await test.step('storage: three projects present, org-scoped', async () => {
    await expectPoll(async () => probe.pg<ProjectRow>(
      'SELECT id FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND deleted = false',
      [[a, b, c], actor.organizationId]), (r) => r.length === 3, POLL.SPAN_VISIBLE);
  });

  await test.step('UI: filter to the prefix and sort ascending, then descending, by the Project column', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await page.getByPlaceholder('Search').fill(prefix);
    for (const name of [a, b, c]) await expect(page.getByText(name)).toBeVisible({ timeout: UI_READY });

    const header = page.getByRole('columnheader', { name: /project/i });
    const waitAsc = page.waitForResponse(
      (r) => r.url().includes(PROJECT_LIST_PATH) && /sort_by=name/.test(r.url())
        && /sort_direction=asc/.test(r.url()) && r.ok(),
      { timeout: UI_READY });
    await header.click();
    await waitAsc;
    // Poll the DOM order: the response arriving is not the same as React
    // re-rendering the re-sorted rows.
    await expect.poll(() => topToBottom(page, [a, b, c]), { timeout: UI_READY }).toEqual([a, b, c]);

    const waitDesc = page.waitForResponse(
      (r) => r.url().includes(PROJECT_LIST_PATH) && /sort_by=name/.test(r.url())
        && /sort_direction=desc/.test(r.url()) && r.ok(),
      { timeout: UI_READY });
    await header.click();
    await waitDesc;
    await expect.poll(() => topToBottom(page, [a, b, c]), { timeout: UI_READY }).toEqual([c, b, a]);
  });

  await test.step('API: the list endpoint returns the same order and never drops a row', async () => {
    const asc = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: prefix, page_number: 0, page_size: 100,
      sort_by: 'name', sort_direction: 'asc',
    });
    expect(asc.map((r) => r.name)).toEqual([a, b, c]);

    const desc = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: prefix, page_number: 0, page_size: 100,
      sort_by: 'name', sort_direction: 'desc',
    });
    expect(desc.map((r) => r.name)).toEqual([c, b, a]);
  });

  await req.dispose();
});

// The order the given names appear top-to-bottom in the grid, by vertical
// position — robust to the grid's cell markup (no data-field guess needed).
async function topToBottom(page: import('@playwright/test').Page, names: string[]): Promise<string[]> {
  const ys = await Promise.all(names.map(async (name) => {
    const box = await page.getByText(name).first().boundingBox();
    return { name, y: box ? box.y : Number.POSITIVE_INFINITY };
  }));
  return ys.sort((x, z) => x.y - z.y).map((e) => e.name);
}

async function expectPoll<T>(
  fn: () => Promise<T>, ok: (v: T) => boolean, opts: { timeout: number; intervals?: number[] },
): Promise<T> {
  let last: T;
  await expect.poll(async () => { last = await fn(); return ok(last); }, opts).toBe(true);
  return last!;
}
