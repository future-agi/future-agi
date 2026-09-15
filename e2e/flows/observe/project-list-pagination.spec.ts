import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app: the project list request (observe-project-list.js,
// page_number 0-based + page_size) and the custom pager footer
// (components/data-table/DataTablePagination.jsx) — a native page-size <select>
// (options 10/25/50), a "{from}–{to} of {total}" label, and prev/next
// IconButtons disabled at the boundaries. Pagination is server-side, not a
// windowed cursor pager.
const PROJECT_LIST_PATH = '/tracer/project/list_projects/';
const UI_READY = 60_000;
const SEED_COUNT = 11; // one more than the smallest page size (10) → two pages

interface ProjectRow { id: string; name: string }

test('OBS-E2E-025: paging through the project list walks a deterministic two-page set', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-025', area: 'observe',
    userGoal: 'A user pages through the Observe project list and the controls and window are correct',
    steps: ['seed eleven projects with a shared prefix',
            'open the list, filter to the prefix, set rows-per-page to 10',
            'read page one and step to page two',
            'read both pages from the list API'],
    backendChecks: ['all eleven seeded projects present in PG, org-scoped',
                    'page one shows 10 of 11 with prev disabled; next moves to page two showing the 11th with next disabled',
                    'the list API returns 10 rows for page_number 0 and the remaining 1 for page_number 1 — disjoint, union of 11'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-obs25-${suffix}`;
  const names = Array.from({ length: SEED_COUNT }, (_, i) =>
    `${prefix}-${String(i).padStart(2, '0')}`);
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };

  await Promise.all(names.map((projectName) => sendTrace(req, { ...cfg, projectName })));
  await testInfo.attach('seeded', { body: JSON.stringify({ prefix, count: SEED_COUNT }), contentType: 'application/json' });

  const ids = await test.step('storage: all eleven present, org-scoped', async () => {
    const rows = await expectPoll(async () => probe.pg<ProjectRow>(
      'SELECT id, name FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND deleted = false',
      [names, actor.organizationId]), (r) => r.length === SEED_COUNT, POLL.SPAN_VISIBLE);
    return new Set(rows.map((r) => r.id));
  });

  await test.step('UI: page one shows 10 of 11 (prev disabled); next → page two shows the 11th (next disabled)', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await page.getByPlaceholder('Search').fill(prefix);

    // Anchor the "loaded" signal on the total, not on a specific project row:
    // with 11 seeded in arbitrary order, which project lands on page one
    // depends on the default page size and sort.
    const pageInfo = page.getByText(/\d+–\d+ of \d+/);
    await expect(pageInfo).toHaveText(/of 11/, { timeout: UI_READY });
    const footer = pageInfo.locator('xpath=..');
    await footer.locator('select').selectOption('10');

    await expect(pageInfo).toHaveText(/1–10 of 11/, { timeout: UI_READY });
    const prevBtn = footer.getByRole('button').first();
    const nextBtn = footer.getByRole('button').last();
    await expect(prevBtn).toBeDisabled();
    await expect(nextBtn).toBeEnabled();

    await nextBtn.click();
    await expect(pageInfo).toHaveText(/11–11 of 11/, { timeout: UI_READY });
    await expect(nextBtn).toBeDisabled();
    await expect(prevBtn).toBeEnabled();
  });

  await test.step('API: page 0 returns 10, page 1 returns the remaining 1 — disjoint, union of 11', async () => {
    const p0 = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: prefix, page_number: 0, page_size: 10,
    });
    const p1 = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: prefix, page_number: 1, page_size: 10,
    });
    const p0ids = p0.map((r) => r.id);
    const p1ids = p1.map((r) => r.id);
    expect(p0ids).toHaveLength(10);
    expect(p1ids).toHaveLength(1);
    expect(p0ids.some((id) => p1ids.includes(id))).toBe(false);
    // Union of the two pages is exactly the eleven seeded ids — no leakage, no drop.
    expect([...p0ids, ...p1ids].sort()).toEqual([...ids].sort());
  });

  await req.dispose();
});

async function expectPoll<T>(
  fn: () => Promise<T>, ok: (v: T) => boolean, opts: { timeout: number; intervals?: number[] },
): Promise<T> {
  let last: T;
  await expect.poll(async () => { last = await fn(); return ok(last); }, opts).toBe(true);
  return last!;
}
