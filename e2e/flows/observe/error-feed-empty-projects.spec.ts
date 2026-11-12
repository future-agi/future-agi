import type { Page, Response, Route } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/api/errorFeed/error-feed.js and src/api/project/observe-project-list.js.
const FEED_LIST = '/tracer/feed/issues/';
const PROJECT_LIST = '/tracer/project/list_projects/';
// tracer/serializers/project.py:ProjectSerializer and views/project.py:ProjectViewSet.create.
const PROJECT_CREATE = '/tracer/project/';
// e2e/stack/backend/e2e_feed_issue.py:E2EIssueRequest (test-image-only; not a detector).
const ISSUE_SEED = '/tracer/e2e/feed-issue/';
// frontend/src/layouts/dashboard/config-navigation.jsx and src/routes/paths.js.
const OBSERVE_PAGE = '/dashboard/observe';
const FEED_PAGE = '/dashboard/error-feed';
// frontend/src/pages/dashboard/error-feed/components/ErrorFeedTable.jsx.
const NO_PROJECTS = 'No Observe projects in this workspace.';
const NO_ERRORS = 'No errors — everything looks good!';
const FEED_FAILED = "Couldn't load the Error Feed";
// ErrorFeedTable's MUI Alert plus components/snackbar/styles.js's error variant.
const ERROR_NOTICE = '[role="alert"], .notistack-MuiContent-error';
// README: one browser action's first-paint budget, not a storage poll.
const UI_READY = 60_000;

// Exact wire envelopes: tracer/serializers/feed.py:FeedListApiResponseSerializer,
// tracer/serializers/project.py and e2e/stack/backend/e2e_feed_issue.py.
type Issue = { cluster_id: string; error: { name: string }; project_id: string };
type FeedBody = { status: boolean; result: { data: Issue[]; total: number; limit: number; offset: number } };
type ProjectBody = { status: boolean; result: { table: { id: string; name: string }[]; metadata: { total_rows: number } } };
type Seed = { issue_id: string; cluster_id: string };
type ExpectedIssue = { cluster_id: string; title: string; project_id: string };

// Bind the browser's actual read, not an independent request or a cached UI state.
function isRead(response: Response, path: string) {
  const url = new URL(response.url());
  return url.origin === new URL(E2E.apiUrl).origin && url.pathname === path
    && response.request().method() === 'GET';
}

const issueIdentity = (rows: Issue[]): ExpectedIssue[] => rows.map(row => ({
  cluster_id: row.cluster_id, title: row.error.name, project_id: row.project_id,
})).sort((a, b) => a.title.localeCompare(b.title));

test('OBS-E2E-060: Error Feed stays error-free without projects and Retry restores existing issues', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-060', area: 'observe',
    userGoal: 'A developer opens Error Feed in an empty workspace without a spurious error, reads existing issues in a populated workspace, and recovers from a failed read using Retry.',
    steps: [
      'open Observe in a test-owned empty workspace and click Error Feed',
      'read the no-projects status without an error notification on first visit, reload and revisit',
      'create a project and two uniquely titled issues through the existing test-image issue seeder in the original workspace',
      'open Error Feed in the populated workspace and read exactly the seeded issue titles',
      'inject a browser-only feed-list 500 and read the inline error and Retry, never a success empty state',
      'remove the injected fault and click Retry to recover the same issue set from fresh real feed and project reads',
    ],
    backendChecks: [
      'The empty workspace has zero live Observe projects in PG; its real project catalog and feed list return successful exact empty results on first visit, reload and revisit.',
      'The project retains its organization/workspace binding and the seeded issues retain their exact IDs, titles and project binding in PG tracer_trace_error_group; the real feed response and rendered table agree on the exact issue set. This does not qualify automatic detection or trace membership.',
      'After the browser-only injected failure, Retry performs fresh real successful feed and catalog reads and restores exactly the seeded issues while removing the inline error.',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(585_000); // 60s seed + eight UI_READY phases (480s) + 45s headroom; no collector wait.
  const uiExpect = expect.configure({ timeout: UI_READY });
  const actor = scopeActors.ownerA;
  const emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-obs60-${suffix}`;
  const titles = [`e2e-obs60-alpha-${suffix}`, `e2e-obs60-beta-${suffix}`];
  const notices: string[] = [];
  const context = await scopeActors.openContext(browser, emptyActor);
  // Observe transient alerts from the first document onward; checking only the final
  // DOM would miss an error toast that disappeared during navigation or loading.
  await context.exposeBinding('recordFeedError', (_source, text: string) => { notices.push(text); });
  await context.addInitScript(selector => {
    const seen = new WeakSet<Element>();
    const scan = (node: Node) => {
      if (!(node instanceof Element)) return;
      const matches = [...node.querySelectorAll(selector)];
      if (node.matches(selector)) matches.unshift(node);
      for (const element of matches) {
        if (seen.has(element)) continue;
        seen.add(element);
        void (window as unknown as { recordFeedError: (text: string) => Promise<void> })
          .recordFeedError(element.textContent ?? '');
      }
    };
    new MutationObserver(records => {
      for (const record of records) {
        scan(record.target);
        for (const node of record.addedNodes) scan(node);
      }
    }).observe(document, { childList: true, subtree: true, attributes: true, characterData: true });
  }, ERROR_NOTICE);
  const emptyPage = await context.newPage();
  emptyPage.setDefaultTimeout(UI_READY);
  emptyPage.setDefaultNavigationTimeout(UI_READY);

  const readPair = async (page: Page, who: ScopeActor, action: () => Promise<unknown>, label: string) => {
    const [catalog, feed] = await Promise.all([
      page.waitForResponse(response => isRead(response, PROJECT_LIST), { timeout: UI_READY }),
      page.waitForResponse(response => isRead(response, FEED_LIST), { timeout: UI_READY }),
      action(),
    ]);
    for (const response of [catalog, feed]) {
      const headers = await response.request().allHeaders();
      expect(headers['x-organization-id']).toBe(who.organizationId);
      expect(headers['x-workspace-id']).toBe(who.workspaceId);
      expect(response.status(), `${label}: ${new URL(response.url()).pathname}`).toBe(200);
    }
    const projectBody = await catalog.json() as ProjectBody;
    const feedBody = await feed.json() as FeedBody;
    await testInfo.attach(`${label}-live-reads`, { contentType: 'application/json', body: JSON.stringify({
      organizationId: who.organizationId, workspaceId: who.workspaceId,
      catalog: { status: catalog.status(), body: projectBody }, feed: { status: feed.status(), body: feedBody },
    }) });
    expect(projectBody.status).toBe(true);
    expect(feedBody.status).toBe(true);
    return { projectBody, feedBody };
  };

  const assertEmpty = async (label: string, action: () => Promise<unknown>) => {
    const { projectBody, feedBody } = await readPair(emptyPage, emptyActor, action, label);
    expect(projectBody.result.table).toEqual([]);
    expect(projectBody.result.metadata.total_rows).toBe(0);
    expect(feedBody.result).toEqual({ data: [], total: 0, limit: 25, offset: 0 });
    await uiExpect(emptyPage.getByRole('status').getByText(NO_PROJECTS, { exact: true })).toBeVisible();
    await uiExpect(emptyPage.getByText(NO_ERRORS, { exact: true })).toHaveCount(0);
    await uiExpect(emptyPage.locator(ERROR_NOTICE)).toHaveCount(0);
    // Flush the observer/binding on this document before a reload can destroy it.
    await emptyPage.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(notices, 'no transient error alerts during the empty-workspace visits').toEqual([]);
    await testInfo.attach(`${label}-empty-state`, { body: await emptyPage.screenshot(), contentType: 'image/png' });
  };

  const emptyProjects = await probe.pg<{ id: string }>(
    `SELECT id FROM tracer_project WHERE organization_id=$1 AND workspace_id=$2
     AND trace_type='observe' AND deleted=false`, [emptyActor.organizationId, emptyActor.workspaceId]);
  expect(emptyProjects).toEqual([]);
  await testInfo.attach('empty-workspace', { contentType: 'application/json', body: JSON.stringify({
    userId: emptyActor.userId, organizationId: emptyActor.organizationId,
    workspaceId: emptyActor.workspaceId, liveObserveProjects: emptyProjects,
  }) });

  await test.step('open Observe in the explicitly empty workspace', async () => {
    await emptyPage.goto(OBSERVE_PAGE, { waitUntil: 'domcontentloaded' });
    await uiExpect(emptyPage.getByRole('link', { name: 'Error Feed', exact: true })).toBeVisible();
  }, { timeout: UI_READY });
  await test.step('first Error Feed visit is a successful no-projects state', async () => {
    await assertEmpty('first-visit', () => emptyPage.getByRole('link', { name: 'Error Feed', exact: true }).click());
    await uiExpect(emptyPage).toHaveURL(new RegExp(`${FEED_PAGE}/?$`));
  }, { timeout: UI_READY });
  await test.step('reload keeps the empty workspace error-free', async () => {
    await assertEmpty('reload', () => emptyPage.reload({ waitUntil: 'domcontentloaded' }));
  }, { timeout: UI_READY });
  await test.step('navigate back to Observe', async () => {
    await emptyPage.getByRole('link', { name: 'Tracing', exact: true }).click();
    await uiExpect(emptyPage).toHaveURL(new RegExp(`${OBSERVE_PAGE}/?$`));
    await uiExpect(emptyPage.getByText(NO_PROJECTS, { exact: true })).toHaveCount(0);
  }, { timeout: UI_READY });
  await test.step('revisit revalidates both reads even within their freshness windows', async () => {
    await assertEmpty('revisit', () => emptyPage.getByRole('link', { name: 'Error Feed', exact: true }).click());
  }, { timeout: UI_READY });
  await context.close();

  // ProjectSerializer and ProjectViewSet.create; no ingestion key is transplanted
  // to the extra workspace. The issue seeder does not establish trace membership.
  const created = await actor.api.post<{ result: { project_id: string; name: string } }>(PROJECT_CREATE, {
    name: projectName, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  const projectId = created.result.project_id;
  const seeds: Seed[] = [];
  const expected: ExpectedIssue[] = [];
  await testInfo.attach('seeded-project', { contentType: 'application/json', body: JSON.stringify({
    userId: actor.userId, organizationId: actor.organizationId, workspaceId: actor.workspaceId, projectId, projectName,
  }) });
  for (const title of titles) {
    const seed = await actor.api.post<Seed>(ISSUE_SEED, {
      project_id: projectId, title, source: 'scanner', status: 'for_review', severity: 'high', occurrences: 1,
    });
    seeds.push(seed);
    expected.push({ cluster_id: seed.cluster_id, title, project_id: projectId });
    await testInfo.attach(`seeded-issue-${seeds.length}`, { contentType: 'application/json', body: JSON.stringify({ ...seed, title, projectId }) });
  }
  const projects = await probe.pg<{ id: string; name: string; organization_id: string; workspace_id: string }>(
    'SELECT id, name, organization_id, workspace_id FROM tracer_project WHERE id=$1 AND deleted=false', [projectId]);
  expect(projects).toEqual([{ id: projectId, name: projectName, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
  // TraceErrorGroup.Meta.db_table; immediate read after synchronous fixture writes.
  const stored = await probe.pg<{ id: string; cluster_id: string; title: string; project_id: string }>(
    'SELECT id, cluster_id, title, project_id FROM tracer_trace_error_group WHERE project_id=$1 AND deleted=false ORDER BY title', [projectId]);
  expect(stored).toEqual(expected.map((issue, index) => ({ id: seeds[index].issue_id, ...issue })));
  const storedTimes = await probe.pg<{ id: string; first_seen: string | null; last_seen: string | null }>(
    'SELECT id, first_seen, last_seen FROM tracer_trace_error_group WHERE project_id=$1 ORDER BY title', [projectId]);
  await testInfo.attach('seeded-issue-times', { contentType: 'application/json', body: JSON.stringify(storedTimes) });
  for (const row of storedTimes) {
    expect(row.first_seen, 'new fixture issues must be visible in the default time window').not.toBeNull();
    expect(row.last_seen).toEqual(row.first_seen);
  }

  const populatedContext = await scopeActors.openContext(browser, actor);
  const page = await populatedContext.newPage();
  page.setDefaultTimeout(UI_READY);
  page.setDefaultNavigationTimeout(UI_READY);
  const assertIssues = async (label: string, action: () => Promise<unknown>) => {
    // Start the live-read witness before acting, but report a failed recovery as
    // missing minted issue titles, not merely a waitForResponse timeout. Handle
    // the parallel read rejection immediately so a UI failure cannot leak it.
    const reads = readPair(page, actor, action, label).then(
      value => ({ value }), error => ({ error }),
    );
    for (const title of titles) await uiExpect(page.getByRole('cell').getByText(title, { exact: true })).toHaveCount(1);
    // Count every body row, not only the title matches: extra issues must fail.
    await uiExpect(page.getByRole('table').locator('tbody tr')).toHaveCount(titles.length);
    const outcome = await reads;
    if ('error' in outcome) throw outcome.error;
    const { projectBody, feedBody } = outcome.value;
    expect(projectBody.result.table.map(row => ({ id: row.id, name: row.name }))).toEqual([{ id: projectId, name: projectName }]);
    expect(projectBody.result.metadata.total_rows).toBe(1);
    expect(feedBody.result.total).toBe(expected.length);
    expect(issueIdentity(feedBody.result.data)).toEqual(expected);
    await uiExpect(page.getByRole('alert').filter({ hasText: FEED_FAILED })).toHaveCount(0);
    await uiExpect(page.getByText(NO_PROJECTS, { exact: true })).toHaveCount(0);
    await uiExpect(page.getByText(NO_ERRORS, { exact: true })).toHaveCount(0);
    await testInfo.attach(`${label}-issues`, { body: await page.screenshot(), contentType: 'image/png' });
  };
  await test.step('populated Error Feed renders exactly the persisted seeded issues', async () => {
    await assertIssues('populated', () => page.goto(FEED_PAGE, { waitUntil: 'domcontentloaded' }));
  }, { timeout: UI_READY });

  let injectedReads = 0;
  const matchFeed = (url: URL) => url.origin === new URL(E2E.apiUrl).origin && url.pathname === FEED_LIST;
  const injectFailure = async (route: Route) => {
    if (route.request().method() !== 'GET') return route.continue();
    injectedReads++;
    // Browser-only fault injection, including automatic query retries. Successful
    // responses are never fabricated: unroute restores the real backend below.
    await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({
      status: false, error: { message: 'OBS-E2E-060 injected feed read failure' },
    }) });
  };
  await page.route(matchFeed, injectFailure);
  try {
    await test.step('a failed feed read shows the inline error with Retry, not success copy', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      const alert = page.getByRole('alert').filter({ hasText: FEED_FAILED });
      await uiExpect(alert.getByText(FEED_FAILED, { exact: true })).toBeVisible();
      await uiExpect(alert.getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
      await uiExpect(page.getByText(NO_PROJECTS, { exact: true })).toHaveCount(0);
      await uiExpect(page.getByText(NO_ERRORS, { exact: true })).toHaveCount(0);
      expect(injectedReads).toBeGreaterThan(0);
      await testInfo.attach('injected-failure', { contentType: 'application/json', body: JSON.stringify({ browserOnly: true, status: 500, path: FEED_LIST, injectedReads }) });
      await testInfo.attach('inline-error', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
    await page.unroute(matchFeed, injectFailure);
    await test.step('Retry fetches fresh real feed and catalog responses and recovers', async () => {
      await assertIssues('retry-recovery', () => page.getByRole('alert').getByRole('button', { name: 'Retry', exact: true }).click());
    }, { timeout: UI_READY });
  } finally {
    await page.unroute(matchFeed, injectFailure);
    // scopeActors owns and disposes every API/browser context, including on failure.
  }
});
