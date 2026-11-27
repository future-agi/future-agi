import type { Page, Response } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/routes/paths.js (dashboard.develop, dashboard.observe) and
// routes/sections/dashboard.jsx (develop/:dataset, observe/:observeId/llm-tracing).
const DATASET_LIST_PAGE = '/dashboard/develop';
const OBSERVE_LIST_PAGE = '/dashboard/observe';
// DevelopDetailView.jsx's availability read: endpoints.develop.getDatasetDetail
// with column_config_only (model_hub/views/develop_dataset.py:GetDatasetTableView).
const datasetRead = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
// ObservePage.jsx's useGetProjectDetails: endpoints.project.projectExperimentDetail
// (tracer/views/project.py:ProjectViewSet.retrieve).
const projectRead = (id: string) => `/tracer/project/${id}/`;
// ObserveListView.jsx:fetchObserveProjects.
const PROJECT_LIST = '/tracer/project/list_projects/';
// tracer/serializers/project.py:ProjectSerializer and ProjectViewSet.create, as OBS-E2E-060 sends it.
const PROJECT_CREATE = '/tracer/project/';
// sections/project/ProjectWrapperView.jsx: the Observe list's delete sends
// DELETE /tracer/project/ {project_ids, project_type: 'observe'} (ProjectViewSet.delete).
const PROJECT_DELETE = '/tracer/project/';
// tfc/utils/error_codes.py DATASET_NOT_FOUND / PROJECT_NOT_FOUND: the legacy 400
// messages the page treats as not found (resourceUnavailableKind.js). A 404 is
// the standard refusal the page accepts as well.
const DATASET_GONE = 'The specified dataset does not exist or has been deleted.';
const PROJECT_GONE = 'Project Not Found';
// sections/common/ResourceUnavailable/ResourceUnavailable.jsx, with the labels
// DevelopDetailView.jsx and ObservePage.jsx pass it.
const DATASET_TITLE = "This dataset doesn't exist or you don't have access";
const PROJECT_TITLE = "This project doesn't exist or you don't have access";
const DATASET_BACK = 'Back to datasets';
const PROJECT_BACK = 'Back to Observe';
// The dataset list's primary action, as DATA-E2E-001/002 open it.
const ADD_DATASET = /^add dataset$/i;
// README: one browser action's first-paint budget, not a storage poll.
const UI_READY = 60_000;

// Exact envelopes: GeneralMethods._error_response_body (tfc/utils/general_methods.py)
// and the project list's { result: { table } } read by StateProbe.apiList.
type Refusal = { status: boolean; code: string; message: string };
type ProjectList = { result: { table: { id: string; name: string }[] } };

// Bind the browser's own read of one path, never an independent request.
function browserRead(page: Page, path: string) {
  return page.waitForResponse((r: Response) => {
    const url = new URL(r.url());
    return url.origin === new URL(E2E.apiUrl).origin && url.pathname === path
      && r.request().method() === 'GET';
  }, { timeout: UI_READY });
}

// The page shows the recovery state for a 404, or for the legacy 400 only with its exact message.
async function expectNotFoundRefusal(response: Response, legacyMessage: string) {
  const body = await response.json() as Refusal;
  const refusal = { status: response.status(), message: body.message };
  expect([{ status: 404, message: body.message }, { status: 400, message: legacyMessage }]).toContainEqual(refusal);
}

test('DATA-E2E-005: a missing dataset or deleted Observe project link shows a recovery page that returns to its list', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-005', area: 'datasets',
    userGoal: 'A user who opens a link to a dataset that does not exist, or to an Observe project that was deleted, sees that it is unavailable and goes back to the list instead of waiting on a loading page.',
    steps: [
      'open a dataset link whose id matches no dataset',
      'read the "doesn\'t exist or you don\'t have access" message and click Back to datasets',
      'land on the dataset list',
      'create two Observe projects through the API and open one of them',
      'delete that project through the API (the Observe list\'s delete request) and reopen its link',
      'read the same message for the project and click Back to Observe',
      'land on the Observe list, which shows the other project and not the deleted one',
    ],
    backendChecks: [
      'no Postgres model_hub_dataset row exists for the opened dataset id, and the browser\'s own dataset read is refused as not found (404, or 400 with the exact missing-dataset message)',
      'the deleted project keeps its Postgres tracer_project row in the actor organization, marked deleted, while the other project stays live',
      'the browser\'s project read answers 200 before the delete and is refused as not found after it (404, or 400 with the exact missing-project message)',
      'the Observe list read after Back to Observe includes the live project and not the deleted one',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // 9 browser waits x UI_READY (540 s) is the worst case; the API writes and
  // PG reads are immediate. The default 120 s would end a slow run as a bare timeout.
  test.setTimeout(600_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const missingDatasetId = randomUUID();

  await test.step('storage: the dataset id matches no row', async () => {
    const rows = await probe.pg<{ n: string }>('SELECT count(*) AS n FROM model_hub_dataset WHERE id = $1', [missingDatasetId]);
    expect(rows[0].n).toBe('0');
  });
  await testInfo.attach('missing-dataset', { contentType: 'application/json',
    body: JSON.stringify({ missingDatasetId, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });

  await test.step('UI + API: the missing dataset link shows the recovery state', async () => {
    const read = browserRead(page, datasetRead(missingDatasetId));
    await page.goto(`${DATASET_LIST_PAGE}/${missingDatasetId}`, { waitUntil: 'domcontentloaded' });
    await expectNotFoundRefusal(await read, DATASET_GONE);
    const state = page.getByRole('alert').filter({ hasText: DATASET_TITLE });
    await expect(state.getByText(DATASET_TITLE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(state.getByRole('link', { name: DATASET_BACK })).toHaveAttribute('href', DATASET_LIST_PAGE);
  });

  await test.step('UI: Back to datasets returns to the dataset list', async () => {
    await page.getByRole('link', { name: DATASET_BACK }).click();
    await expect(page).toHaveURL((url) => url.pathname === DATASET_LIST_PAGE, { timeout: UI_READY });
    await expect(page.getByRole('button', { name: ADD_DATASET }).first()).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(DATASET_TITLE)).toHaveCount(0);
  });

  // The doomed project is created first so the live one is the newest row on the list's first page.
  const doomedName = `e2e-data5-gone-${suffix}`;
  const liveName = `e2e-data5-live-${suffix}`;
  const create = (name: string) => actor.api.post<{ result: { project_id: string } }>(PROJECT_CREATE, {
    name, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  const doomedId = (await create(doomedName)).result.project_id;
  const liveId = (await create(liveName)).result.project_id;
  await testInfo.attach('seeded-projects', { contentType: 'application/json', body: JSON.stringify({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, doomedId, doomedName, liveId, liveName,
  }) });
  const projectPage = `${OBSERVE_LIST_PAGE}/${doomedId}/llm-tracing`;

  await test.step('UI + API: the project link opens while the project exists', async () => {
    const read = browserRead(page, projectRead(doomedId));
    await page.goto(projectPage, { waitUntil: 'domcontentloaded' });
    expect((await read).status()).toBe(200);
    await expect(page.getByText(PROJECT_TITLE)).toHaveCount(0);
  });

  await test.step('API + storage: delete the project as the Observe list does', async () => {
    const deleted = await actor.api.request('delete', PROJECT_DELETE, {
      data: { project_ids: [doomedId], project_type: 'observe' },
    });
    expect(deleted.status).toBe(200);
    const rows = await probe.pg<{ id: string; deleted: boolean }>(
      'SELECT id, deleted FROM tracer_project WHERE id = ANY($1::uuid[]) AND organization_id = $2 ORDER BY name',
      [[doomedId, liveId], actor.organizationId]);
    expect(rows).toEqual([{ id: doomedId, deleted: true }, { id: liveId, deleted: false }]);
  });

  await test.step('UI + API: reopening the deleted project link shows the recovery state', async () => {
    const read = browserRead(page, projectRead(doomedId));
    await page.goto(projectPage, { waitUntil: 'domcontentloaded' });
    await expectNotFoundRefusal(await read, PROJECT_GONE);
    const state = page.getByRole('alert').filter({ hasText: PROJECT_TITLE });
    await expect(state.getByText(PROJECT_TITLE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(state.getByRole('link', { name: PROJECT_BACK })).toHaveAttribute('href', OBSERVE_LIST_PAGE);
  });

  await test.step('UI + API: Back to Observe returns to a list without the deleted project', async () => {
    const list = browserRead(page, PROJECT_LIST);
    await page.getByRole('link', { name: PROJECT_BACK }).click();
    await expect(page).toHaveURL((url) => url.pathname === OBSERVE_LIST_PAGE, { timeout: UI_READY });
    const listed = (await (await list).json() as ProjectList).result.table.map(p => p.id);
    expect(listed).toContain(liveId);
    expect(listed).not.toContain(doomedId);
    await expect(page.getByText(liveName, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(doomedName, { exact: true })).toHaveCount(0);
    await expect(page.getByText(PROJECT_TITLE)).toHaveCount(0);
  });
});
