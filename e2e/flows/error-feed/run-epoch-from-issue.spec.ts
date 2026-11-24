import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';
import { SHARED_ISSUE } from './shared-issue.payload';
import {
  ENV_ID,
  ENV_NAME,
  ISSUE_TITLE,
  PROJECT_ID,
  RUN_BODY,
  createBody,
  issueJobDetail,
  issuePlan,
  issueStatusList,
  readyEpoch,
  type Ids,
  type IssuePhase,
} from './run-epoch-from-issue.payload';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license), no Error Feed seeder, no builder and no simulation runner,
// so it cannot hold an issue, a drafted import, a baseline or an epoch. Stub only
// those responses here, as FEED-E2E-001 and SIM-E2E-007/008 do. The stubs live in
// run-epoch-from-issue.payload.ts, which the frontend suite parses with the
// generated contract (epochFromIssueStubs.test.js), so they cannot drift from the
// API. The issue page, the drawer, its requests, the status strip, the epoch page's
// breadcrumb and the list icon are the product's own. The real import → draft →
// baseline → epoch path is covered by the core PR's backend tests.

// Browser-side waits: one navigation's or one click's first paint.
const UI_READY = 60_000;

// What the screens say: issueEpochView.js and RunEpochDrawer.jsx in
// frontend/src/pages/dashboard/error-feed/epoch/.
const MEASURING_LINE =
  'Running the reviewed scenarios on the current version. The epoch starts when that run ends.';
const READY_LINE = 'Epoch 3 found a change that passed every check.';
const ICON_TIP = 'Epoch 3 · Ready for review';

type Json = Record<string, unknown>;

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubApi(page: Page, ids: Ids) {
  const seen = {
    planParams: [] as Record<string, string>[],
    createBodies: [] as Json[],
    runBodies: [] as Json[],
    batchParams: [] as string[],
  };
  // Where the job is: nothing yet, then what each request moves it to.
  const state: { phase: IssuePhase | null } = { phase: null };
  const issueRow = { ...SHARED_ISSUE.issue.row, cluster_id: ids.cluster, project_id: PROJECT_ID };

  // The real capabilities, with the Error Feed and Epoch switched on.
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await route.fetch();
    const json = await response.json();
    json.features = {
      ...json.features,
      error_feed: { ...json.features?.error_feed, allowed: true },
      fix_my_agent: { ...json.features?.fix_my_agent, allowed: true },
    };
    await route.fulfill({ response, json });
  });
  // The issue's own reads: the captured shared-issue responses, under this issue's id.
  const issuePath = `/tracer/feed/issues/${ids.cluster}/`;
  await page.route((url) => url.pathname.includes(issuePath), (route) => {
    const rest = new URL(route.request().url()).pathname.split(issuePath)[1];
    const result: Record<string, unknown> = {
      '': { ...SHARED_ISSUE.issue, row: issueRow },
      'redirect/': { requested_cluster_id: ids.cluster, resolved_cluster_id: ids.cluster },
      'overview/': SHARED_ISSUE.overview,
      'traces/': SHARED_ISSUE.traces,
      'trends/': SHARED_ISSUE.trends,
      'sidebar/': SHARED_ISSUE.sidebar,
    };
    if (rest in result) return route.fulfill({ json: { status: true, result: result[rest] } });
    return route.fulfill({ status: 404, json: { status: false } });
  });
  // The list: one row, this issue (FeedListApiResponseSerializer).
  await page.route(endsWith('/tracer/feed/issues/'), (route) =>
    route.fulfill({ json: { status: true, result: { data: [issueRow], total: 1, limit: 25, offset: 0 } } }),
  );
  // The environments list (HarnessEnvironmentListResponseSerializer): one GitHub
  // agent's environment and one hosted agent's, which the picker must leave out.
  const envRow = (id: string, name: string, sourceKind: string) => ({
    id, name, description: null, domain: null, source_kind: sourceKind, agent_type: 'chat',
    status: 'completed', stage: 'completed', scenario_count: 12, sub_goals_count: null,
    tools_count: null, runs_count: 1, last_updated: null, created_at: '2026-11-10T09:00:00Z', last_run: null,
  });
  await page.route(endsWith('/simulate/api/harness-environments/'), (route) =>
    route.fulfill({
      json: {
        count: 2, next: null, previous: null, total_pages: 1, current_page: 1,
        results: [envRow(ENV_ID, ENV_NAME, 'github'), envRow(randomUUID(), 'Phone line', 'provider')],
      },
    }),
  );
  await page.route(endsWith('/simulate/api/agent-fix-issues/plan/'), (route) => {
    const params = Object.fromEntries(new URL(route.request().url()).searchParams);
    seen.planParams.push(params);
    return route.fulfill({ json: issuePlan(ids.cluster, !!params.environment_id) });
  });
  await page.route(endsWith('/simulate/api/agent-fix-issues/'), (route) => {
    if (route.request().method() === 'POST') {
      seen.createBodies.push(route.request().postDataJSON() as Json);
      // The builder drafts at once here: the next read finds the drafts ready.
      state.phase = 'review';
      return route.fulfill({ status: 201, json: issueJobDetail(ids, 'drafting') });
    }
    seen.batchParams.push(new URL(route.request().url()).searchParams.get('cluster_ids') ?? '');
    return route.fulfill({ json: issueStatusList(ids, state.phase) });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-issues/${ids.job}/`), (route) =>
    route.fulfill({ json: issueJobDetail(ids, state.phase ?? 'review') }),
  );
  await page.route(endsWith(`/simulate/api/agent-fix-issues/${ids.job}/run/`), (route) => {
    seen.runBodies.push(route.request().postDataJSON() as Json);
    state.phase = 'measuring';
    return route.fulfill({ status: 202, json: issueJobDetail(ids, 'measuring') });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${ids.epoch}/`), (route) =>
    route.fulfill({ json: readyEpoch(ids) }),
  );
  return { seen, state };
}

test('ERR-E2E-006: a developer runs an Epoch from an Error Feed issue and follows it from the issue', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ERR-E2E-006', area: 'error-feed',
    userGoal: 'A developer runs an Epoch from an Error Feed issue: picks its environment once, reviews the slim plan and the drafted scenarios, starts it, and the issue page, the epoch page and the issue list show that epoch and its next action',
    steps: [
      'open the issue', 'press Run Epoch', 'pick the environment', 'read the plan and swap one trace',
      'press Draft scenarios', 'untick one drafted scenario', 'press Run Epoch in the review',
      'read the status strip', 'reload once the epoch is ready', 'read the header action',
      'press View Epoch 3', 'reload the epoch page', 'open the issue list and hover the epoch icon',
    ],
    browserChecks: [
      'the plan is read only after Run Epoch, first with the issue and project, then with the picked environment, and the picker lists only the code agent\'s environment',
      'the create POST body is exactly the issue, project, environment and the picks after the swap',
      'the run POST body is exactly the kept scenario and the review\'s launch rows at their defaults',
      'the strip reads Measuring with its sentence, then Ready for review with Open pull request as the header action after a reload',
      'the epoch page\'s breadcrumb is the environment linking to its Epochs tab, the header reads "From issue: <title>" linking to the issue, and both survive a reload',
      'the list reads the page\'s cluster ids in one batch and the issue\'s icon reads "Epoch 3 · Ready for review"',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 13 browser waits, each its own UI_READY: 13 × 60s = 780s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(840_000);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`.toUpperCase();
  const ids: Ids = {
    cluster: `S-E6${suffix}`.slice(0, 20),
    job: randomUUID(),
    epoch: randomUUID(),
    candidate: randomUUID(),
  };
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, projectId: PROJECT_ID, ...ids }),
    contentType: 'application/json',
  });
  const { seen, state } = await stubApi(page, ids);
  const drawer = page.getByTestId('run-epoch-drawer');
  const strip = page.getByTestId('issue-epoch-strip');

  await test.step('ui: the issue page offers Run Epoch and reads no plan', async () => {
    await page.goto(`/dashboard/error-feed/${ids.cluster}`);
    await expect(page.getByTestId('issue-run-epoch')).toHaveText('Run Epoch', { timeout: UI_READY });
    expect(seen.planParams).toEqual([]);
  }, { timeout: UI_READY });

  await test.step('ui: check 1, the first plan asks where it runs, listing code agents only', async () => {
    await page.getByTestId('issue-run-epoch').click();
    // Exactly one choice, and it is the GitHub agent's environment.
    const list = drawer.getByTestId('epoch-environments');
    await expect(list.getByRole('radio')).toHaveCount(1, { timeout: UI_READY });
    await expect(list.getByText(ENV_NAME, { exact: true })).toBeVisible();
    expect(seen.planParams).toEqual([{ cluster_id: ids.cluster, project_id: PROJECT_ID }]);
  }, { timeout: UI_READY });

  await test.step('ui: check 1, the plan is read again for the picked environment', async () => {
    await drawer.getByRole('radio').check();
    await drawer.getByRole('button', { name: 'Continue' }).click();
    await expect(drawer.getByTestId('epoch-pick')).toHaveCount(2, { timeout: UI_READY });
    expect(seen.planParams[1]).toEqual({ cluster_id: ids.cluster, project_id: PROJECT_ID, environment_id: ENV_ID });
    await expect(drawer.getByText('One builder turn, billed as authoring credits.', { exact: true })).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('ui: check 2, Draft scenarios sends the picks after the swap', async () => {
    await drawer.getByTestId('epoch-pick').first().getByRole('button', { name: 'Swap' }).click();
    await drawer.getByTestId('epoch-draft-scenarios').click();
    await expect.poll(() => seen.createBodies.length, { timeout: UI_READY }).toBe(1);
    expect(seen.createBodies[0]).toEqual(createBody(ids.cluster));
  }, { timeout: UI_READY });

  await test.step('ui: check 3, the review runs the kept scenario with the launch rows', async () => {
    const keep = drawer.getByRole('checkbox', { name: 'Keep this scenario' });
    await expect(keep).toHaveCount(2, { timeout: UI_READY });
    await keep.nth(1).uncheck();
    await drawer.getByTestId('epoch-run').click();
    await expect.poll(() => seen.runBodies.length, { timeout: UI_READY }).toBe(1);
    expect(seen.runBodies[0]).toEqual(RUN_BODY);
  }, { timeout: UI_READY });

  await test.step('ui: check 4, the strip reads Measuring', async () => {
    await page.keyboard.press('Escape');
    await expect(strip.getByText(MEASURING_LINE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(strip.getByText('Measuring', { exact: true })).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('ui: check 4, a ready epoch makes Open pull request the main action', async () => {
    state.phase = 'launched';
    await page.reload();
    await expect(page.getByRole('button', { name: 'Open pull request' })).toBeVisible({ timeout: UI_READY });
    await expect(strip.getByText('Ready for review', { exact: true })).toBeVisible();
    await expect(strip.getByText(READY_LINE, { exact: true })).toBeVisible();
  }, { timeout: UI_READY });

  const crumb = page.getByTestId('epoch-environment-crumb');
  const origin = page.getByTestId('epoch-origin');
  await test.step('ui: check 5, the epoch page names its environment and the issue it came from', async () => {
    await strip.getByRole('link', { name: 'View Epoch 3' }).click();
    await expect(page).toHaveURL(
      exactPath(`/dashboard/simulate/environments/${ENV_ID}/improvements/${ids.epoch}`), { timeout: UI_READY },
    );
    await expect(crumb).toHaveText(ENV_NAME, { timeout: UI_READY });
    await expect(crumb).toHaveAttribute('href', `/dashboard/simulate/environments/${ENV_ID}?tab=improvements`);
    await expect(origin).toHaveText(`From issue: ${ISSUE_TITLE}`);
    await expect(origin).toHaveAttribute('href', `/dashboard/error-feed/${ids.cluster}`);
  }, { timeout: UI_READY });

  await test.step('ui: check 5, the header survives a reload', async () => {
    await page.reload();
    await expect(crumb).toHaveText(ENV_NAME, { timeout: UI_READY });
    await expect(origin).toHaveText(`From issue: ${ISSUE_TITLE}`, { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 6, the list shows the epoch after the issue name', async () => {
    const before = seen.batchParams.length;
    await page.goto('/dashboard/error-feed');
    const icon = page.getByTestId('issue-epoch-icon');
    await expect(icon).toHaveAttribute('aria-label', ICON_TIP, { timeout: UI_READY });
    await icon.hover();
    await expect(page.getByRole('tooltip')).toHaveText(ICON_TIP, { timeout: UI_READY });
    expect(seen.batchParams[before]).toEqual(ids.cluster);
  }, { timeout: UI_READY });
});
