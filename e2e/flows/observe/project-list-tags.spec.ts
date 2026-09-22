import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app: the per-row TagEditor (sections/project/TagEditor.jsx)
// PATCHes the full tag array to /tracer/project/{id}/tags/ (ProjectView.update_tags),
// which stores tags as a JSONB list on tracer_project.tags. The Tags DataGrid
// column has field "tags" (ObserveListView.jsx id:"tags"); the empty cell is a
// clickable box, and the popover's new-tag input placeholder is exact.
const PROJECT_LIST_PATH = '/tracer/project/list_projects/';
const TAGS_PATH_FRAGMENT = '/tags/';
const NEW_TAG_PLACEHOLDER = 'Type new tag and press Enter';
const UI_READY = 60_000;

const tagsFilter = (value: string) =>
  JSON.stringify([
    { column_id: 'tags', filter_config: { filter_type: 'text', filter_op: 'contains', filter_value: value } },
  ]);

interface ProjectRow { id: string; name: string; tags: string[] }

test('OBS-E2E-024: tagging a project persists the tag and makes it filterable', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-024', area: 'observe',
    userGoal: 'A user tags an Observe project and the tag persists and is filterable',
    steps: ['seed one project',
            'open the list and filter to it',
            "open the project's tag editor and add a new tag",
            'reload and reopen the tag editor',
            'filter the list by the new tag'],
    backendChecks: ['the tag editor PATCHes /tracer/project/{id}/tags/ and PG tracer_project.tags holds the new tag',
                    'the tag chip survives a page reload (persisted, not just local state)',
                    'the list API filtered by the tag returns the project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const project = `e2e-obs24-${suffix}`;
  const tag = `e2e-tag-${suffix}`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };

  await sendTrace(req, { ...cfg, projectName: project });
  await testInfo.attach('seeded', { body: JSON.stringify({ project, tag }), contentType: 'application/json' });

  const projectId = await test.step('storage: the project exists, org-scoped', async () => {
    const rows = await expectPoll(async () => probe.pg<ProjectRow>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND deleted = false',
      [project, actor.organizationId]), (r) => r.length === 1, POLL.SPAN_VISIBLE);
    return rows[0].id;
  });

  const openTagEditor = async () => {
    await page.getByPlaceholder('Search').fill(project);
    await expect(page.getByText(project)).toBeVisible({ timeout: UI_READY });
    await page.getByRole('row').filter({ hasText: project }).locator('[data-field="tags"]').click();
    await expect(page.getByPlaceholder(NEW_TAG_PLACEHOLDER)).toBeVisible({ timeout: UI_READY });
  };

  await test.step('UI: add a new tag through the tag editor; PG holds it', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await openTagEditor();
    const patched = page.waitForResponse(
      (r) => r.url().includes(TAGS_PATH_FRAGMENT) && r.request().method() === 'PATCH' && r.ok(),
      { timeout: UI_READY });
    await page.getByPlaceholder(NEW_TAG_PLACEHOLDER).fill(tag);
    await page.getByPlaceholder(NEW_TAG_PLACEHOLDER).press('Enter');
    const res = await patched;
    const body = res.request().postDataJSON() as { tags: string[] };
    expect(body.tags).toContain(tag);

    await expectPoll(async () => probe.pg<{ tags: string[] }>(
      'SELECT tags FROM tracer_project WHERE id = $1', [projectId]),
      (rows) => rows.length === 1 && rows[0].tags.includes(tag), POLL.SPAN_VISIBLE);
  });

  await test.step('UI: the tag survives a reload (persisted)', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await openTagEditor();
    await expect(page.getByText(tag).first()).toBeVisible({ timeout: UI_READY });
  });

  await test.step('API: the list filtered by the tag returns the project', async () => {
    const rows = await probe.apiList<ProjectRow>(PROJECT_LIST_PATH, {
      project_type: 'observe', page_number: 0, page_size: 100, filters: tagsFilter(tag),
    });
    expect(rows.map((r) => r.id)).toEqual([projectId]);
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
