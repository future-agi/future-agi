import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Saved-view create/rename endpoint (see tracer/urls.py `saved-views`).
const SAVED_VIEWS_PATH = '/tracer/saved-views/';
// Browser-side waits — sized like the sibling observe specs; the local stack
// slows several-fold under parallel runs, well past the 10s expect default.
const UI_READY = 60_000;

test('OBS-E2E-020: duplicate saved-view names are rejected', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-020', area: 'observe',
    userGoal: 'A user cannot silently overwrite an existing observability view by reusing its name',
    steps: ['seed a trace so a project auto-creates',
            'create a project-scoped saved view via the API',
            're-create the same name via the API',
            'open the observe tab bar and try to save the same name in the UI'],
    backendChecks: ['first create returns 200 and persists the view',
                    'second create with the same (project, user, name) returns 400, not a silent upsert',
                    'renaming another view onto the taken name returns 400'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Chains sendTrace + CH poll + PG lookup + navigation + UI waits, past the
  // 120s config default; a bare timeout otherwise hides the real assertion.
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-savedview-${suffix}`;
  const viewName = `Errors ${suffix}`;

  // Seed a trace: the collector auto-creates the project we scope views to.
  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: `e2e.sv-${suffix}`,
  });

  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}',
      { t: seeded.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);

  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, actor.organizationId]);
  expect(projects).toHaveLength(1);
  const projectId = projects[0].id;

  let secondViewId = '';

  await test.step('API: the same name cannot be created twice', async () => {
    const first = await actor.api.post<{ result: { id: string; name: string } }>(
      SAVED_VIEWS_PATH,
      { project_id: projectId, name: viewName, tab_type: 'voice' },
    );
    expect(first.result.name).toBe(viewName);

    // Second create with the same (project, user, name) must be rejected —
    // not silently upserted onto the first view.
    await expect(
      actor.api.post(SAVED_VIEWS_PATH, {
        project_id: projectId, name: viewName, tab_type: 'voice',
      }),
    ).rejects.toMatchObject({ status: 400 });
  });

  await test.step('API: renaming another view onto the taken name is rejected', async () => {
    const second = await actor.api.post<{ result: { id: string } }>(
      SAVED_VIEWS_PATH,
      { project_id: projectId, name: `Other ${suffix}`, tab_type: 'voice' },
    );
    secondViewId = second.result.id;
    // The PATCH must carry project_id — get_object() scopes by the query param,
    // so without it a project-scoped row 404s instead of hitting the rename guard.
    await expect(
      actor.api.patch(
        `${SAVED_VIEWS_PATH}${secondViewId}/?project_id=${projectId}`,
        { name: viewName },
      ),
    ).rejects.toMatchObject({ status: 400 });
  });

  await test.step('UI: the Save View popover blocks a duplicate name', async () => {
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces`,
      { waitUntil: 'domcontentloaded' });
    await page.locator('[data-create-view-btn]').click({ timeout: UI_READY });
    await page.getByLabel(/View Name/).fill(viewName);
    await expect(
      page.getByText('A view with this name already exists.'),
    ).toBeVisible({ timeout: UI_READY });
    await expect(
      page.getByRole('button', { name: 'Save view' }),
    ).toBeDisabled();
  });

  await req.dispose();
});
