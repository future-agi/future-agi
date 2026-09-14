import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app: "Add Project" (ProjectRightSection.jsx) opens the
// NewObserve instrumentation drawer (NewProject/NewObserve.jsx) — SDK snippets
// from GET /tracer/project/project_sdk_code/, NOT a create form. An Observe
// project is created implicitly on first ingestion (otel.py get_or_create_project),
// so a second trace with the same name reuses the row (no duplicate, no error).
// The docs link's accessible name is "Docs"; it is pinned by href to avoid the
// sidebar "Docs" link that points at the docs root.
const SDK_CODE_FRAGMENT = '/project_sdk_code/';
const DOCS_HREF = 'https://docs.futureagi.com/docs/observe';
const UI_READY = 60_000;

interface ProjectRow { id: string }

test('OBS-E2E-026: Add Project shows onboarding and an instrumented project appears once', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-026', area: 'observe',
    userGoal: 'A user opens Add Project, follows the instrumentation, and their newly-instrumented project appears in the list',
    steps: ['instrument a project by sending a trace for a new name',
            'open the Observe project list and find it',
            'click Add Project and read the instrumentation drawer',
            'send a second trace with the same name'],
    backendChecks: ['after ingestion exactly one PG tracer_project row exists for the name, org-scoped, and it shows in the list',
                    'Add Project fetches SDK snippets from /tracer/project/project_sdk_code/ and the drawer shows Setup Instrumentation onboarding',
                    'the Observe docs link points at the Observe docs',
                    'a second trace with the same name creates no duplicate (get-or-create), so the count stays one'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const project = `e2e-obs26-${suffix}`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };
  await testInfo.attach('seeded', { body: JSON.stringify({ project }), contentType: 'application/json' });

  await test.step('storage: instrumenting the project creates exactly one row, org-scoped', async () => {
    await sendTrace(req, { ...cfg, projectName: project });
    await expectPoll(async () => probe.pg<ProjectRow>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND deleted = false',
      [project, actor.organizationId]), (r) => r.length === 1, POLL.SPAN_VISIBLE);
  });

  await test.step('UI: the project shows in the list', async () => {
    await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
    await expect(page.getByPlaceholder('Search')).toBeVisible({ timeout: UI_READY });
    await page.getByPlaceholder('Search').fill(project);
    await expect(page.getByText(project)).toBeVisible({ timeout: UI_READY });
  });

  await test.step('UI: Add Project opens the instrumentation drawer (SDK snippets, no create form)', async () => {
    const sdk = page.waitForResponse(
      (r) => r.url().includes(SDK_CODE_FRAGMENT) && r.ok(), { timeout: UI_READY });
    await page.getByRole('button', { name: /add project/i }).click();
    await sdk;
    await expect(page.getByText(/setup instrumentation/i).first()).toBeVisible({ timeout: UI_READY });
    // The onboarding drawer carries the Observe docs link.
    await expect(page.locator(`a[href="${DOCS_HREF}"]`).first()).toBeVisible({ timeout: UI_READY });
  });

  await test.step('storage (negative): a second trace with the same name creates no duplicate', async () => {
    const second = await sendTrace(req, { ...cfg, projectName: project });
    // Poll until the SECOND trace's spans have actually landed. Otherwise a
    // count of 1 could just mean the write has not happened yet, not that
    // get-or-create deduped — which is the whole claim.
    await expect.poll(async () => {
      const r = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: second.traceId });
      return Number(r[0].n);
    }, POLL.SPAN_VISIBLE).toBe(second.spanIds.length);
    const rows = await probe.pg<ProjectRow>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND deleted = false',
      [project, actor.organizationId]);
    expect(rows).toHaveLength(1);
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
