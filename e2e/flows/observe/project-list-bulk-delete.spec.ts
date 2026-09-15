import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app: the project list request
// (observe-project-list.js readObserveProjectPage) and the bulk-delete call
// (ProjectWrapperView.jsx confirmDelete → endpoints.project.deleteObservePrototype
// = DELETE /tracer/project/, body { project_ids, project_type }). Backend:
// futureagi/tracer/views/project.py ProjectView.delete (soft-delete).
const PROJECT_LIST_PATH = '/tracer/project/list_projects/';
const DELETE_PATH = '/tracer/project/';
const UI_READY = 60_000;

interface ProjectRow { id: string; name: string }

test('OBS-E2E-022: selecting projects and confirming bulk-delete removes them', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-022', area: 'observe',
    userGoal: 'A user selects projects from the Observe list and deletes them in bulk',
    steps: ['seed two projects with a shared prefix',
            'open the list and filter to that prefix',
            'select all rows on the page',
            'open the delete dialog and cancel it',
            'open it again and confirm the delete'],
    backendChecks: ['both seeded projects present in PG tracer_project before delete, org-scoped',
                    'select-all selects exactly the rows on the current page (both seeded)',
                    'cancelling the confirm dialog leaves both projects present',
                    'confirming sends DELETE /tracer/project/ with both ids and project_type=observe, and both rows are soft-deleted (gone from the list API)'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-obs22-${suffix}`;
  const alpha = `${prefix}-alpha`;
  const beta = `${prefix}-beta`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };

  await Promise.all([
    sendTrace(req, { ...cfg, projectName: alpha }),
    sendTrace(req, { ...cfg, projectName: beta }),
  ]);
  await testInfo.attach('seeded', { body: JSON.stringify({ prefix, alpha, beta }), contentType: 'application/json' });

  const ids = await test.step('storage: both projects present pre-delete, org-scoped', async () => {
    const rows = await expectPoll(async () => probe.pg<ProjectRow>(
      'SELECT id, name FROM tracer_project WHERE name IN ($1, $2) AND organization_id = $3 AND deleted = false',
      [alpha, beta, actor.organizationId]), (r) => r.length === 2, POLL.SPAN_VISIBLE);
    return rows.map((r) => r.id).sort();
  });

  await test.step('UI: filter to the prefix and select every row on the page', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await expect(page.getByPlaceholder('Search')).toBeVisible({ timeout: UI_READY });
    await page.getByPlaceholder('Search').fill(prefix);
    await expect(page.getByText(alpha)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(beta)).toBeVisible({ timeout: UI_READY });
    // MUI DataGrid header select-all; scoped so it can't match a row checkbox.
    await page.getByRole('checkbox', { name: /select all/i }).check();
    // The selection toolbar reports the count as "N Selected".
    await expect(page.getByText(/2 selected/i)).toBeVisible({ timeout: UI_READY });
  });

  await test.step('UI (negative): cancelling the confirm dialog leaves both projects', async () => {
    await page.getByRole('button', { name: 'Delete' }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    await dialog.getByRole('button', { name: /cancel/i }).click();
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    const still = await probe.pg<ProjectRow>(
      'SELECT id FROM tracer_project WHERE id = ANY($1) AND deleted = false', [ids]);
    expect(still.map((r) => r.id).sort()).toEqual(ids);
  });

  await test.step('UI + API: confirming deletes both — DELETE carries both ids, rows soft-deleted', async () => {
    // Re-select unconditionally — check() is idempotent — so the confirm never
    // fires with an empty selection, whether or not closing the cancel dialog
    // cleared it.
    await page.getByRole('checkbox', { name: /select all/i }).check();
    await expect(page.getByText(/2 selected/i)).toBeVisible({ timeout: UI_READY });
    const deleted = page.waitForResponse(
      (r) => r.url().includes(DELETE_PATH) && r.request().method() === 'DELETE' && r.ok(),
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Delete' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Delete' }).click();
    const res = await deleted;
    const body = res.request().postDataJSON() as { project_ids: string[]; project_type: string };
    expect(body.project_type).toBe('observe');
    expect([...body.project_ids].sort()).toEqual(ids);

    const remaining = await expectPoll(async () => probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', name: prefix, page_number: 0, page_size: 100,
    }), (rows) => rows.filter((r) => ids.includes(r.id)).length === 0, POLL.SPAN_VISIBLE);
    expect(remaining.filter((r) => ids.includes(r.id))).toHaveLength(0);

    // The rows are soft-deleted (deleted=true), not hard-deleted — the list
    // endpoint filters deleted=false, so "gone from the API" alone would also
    // hold for a hard delete.
    const soft = await probe.pg<ProjectRow>(
      'SELECT id FROM tracer_project WHERE id = ANY($1) AND deleted = true', [ids]);
    expect(soft.map((r) => r.id).sort()).toEqual([...ids].sort());
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
