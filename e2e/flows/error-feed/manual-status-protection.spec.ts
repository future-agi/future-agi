import { test, expect } from '../../lib/error-feed-fixtures';
import { ErrorFeedHarness, IssueReceipt, FixtureProject } from '../../lib/error-feed-harness';
import { flowAnnotation } from '../../lib/flow-meta';
import { chooseStatus, expectFeedStatus, expectUiStatus, openIssue, storedIssue, ISSUES_PATH } from '../../lib/error-feed-ui';
import { UI_READY, readSettings, SETTINGS_PATH } from '../../lib/error-feed-settings';

test('ESC-E2E-003: Preserve user-selected statuses and F6 attachment protection', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'ESC-E2E-003', area: 'error-feed',
    userGoal: 'An engineer keeps any chosen status while automatic trend checks and new findings run',
    steps: ['publish qualifying findings', 'select all four statuses through detail controls, including the current value',
      'use the bulk status action', 'advance growth and quiet boundaries', 'attempt an F6 attachment and inspect rejection'],
    backendChecks: ['PG manual ownership, actor, status revision and F6 protection persist for all selections',
      'real publisher rejects attachment to the protected issue without adding its finding',
      'Feed APIs and UI retain each chosen status after both automatic cycles'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  test.setTimeout(1_200_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  const policy = await readSettings(scopeActors, scopeActors.ownerA);
  expect((await scopeActors.send(scopeActors.ownerA, 'PATCH', SETTINGS_PATH,
    { revision: policy.revision, min_findings: 10, entry_delta: 8 })).status).toBe(200);
  const cases: { project: FixtureProject; issue: IssueReceipt; status: string }[] = [];
  for (const status of ['for_review', 'escalating', 'acknowledged', 'resolved']) {
    const project = await harness.project(`manual-${status}`, 12);
    const issue = (await harness.publish(project)).issue!;
    const { page, context } = await openIssue(browser, scopeActors, issue);
    await chooseStatus(page, issue, status);
    const saved = await storedIssue(scopeProbe, issue);
    expect(saved.status_origin).toBe('manual');
    expect(saved.status_changed_by_id).toBe(scopeActors.ownerA.userId);
    expect(Number(saved.status_revision)).toBe(1);
    const grouping = await scopeProbe.pg<{ protected: boolean }>(
      'SELECT protected FROM tracer_trace_grouping_issue_state WHERE cluster_id=$1', [issue.id]);
    expect(grouping).toEqual([{ protected: true }]);
    await expect(page.getByText('Set by a user', { exact: true })).toBeVisible({ timeout: UI_READY });
    cases.push({ project, issue, status });
    await context.close();
  }
  const bulkProject = await harness.project('manual-bulk', 12);
  const bulkIssue = (await harness.publish(bulkProject)).issue!;
  const context = await scopeActors.openContext(browser, scopeActors.ownerA);
  const page = await context.newPage();
  await page.goto('/dashboard/error-feed', { waitUntil: 'domcontentloaded' });
  const row = page.getByRole('row').filter({ hasText: bulkProject.name });
  await expect(row).toBeVisible({ timeout: UI_READY });
  await row.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Bulk actions' }).click();
  const response = page.waitForResponse(res => new URL(res.url()).pathname === `${ISSUES_PATH}${bulkIssue.cluster_id}/`
    && res.request().method() === 'PATCH');
  await page.getByRole('menuitem', { name: 'Mark as Resolved' }).click();
  expect((await response).status()).toBe(200);
  cases.push({ project: bulkProject, issue: bulkIssue, status: 'resolved' });
  await context.close();
  for (const minute of [10, 15, 20, 25, 90, 95, 100]) await harness.evaluate(bulkProject, minute);
  for (const item of cases) {
    const saved = await storedIssue(scopeProbe, item.issue);
    expect(saved.status).toBe(item.status);
    expect(saved.status_origin).toBe('manual');
    expect(Number(saved.status_revision)).toBe(1);
    await expectFeedStatus(scopeActors, item.project, item.issue, item.status);
    const view = await openIssue(browser, scopeActors, item.issue);
    await expectUiStatus(view.page, item.status);
    await view.context.close();
  }
  const first = cases[0];
  const later = await harness.ingest(first.project.name, 1, harness.at(-10));
  const rejected = await harness.publish(first.project,
    { target_issue_id: first.issue.id, traces: [later], now: 105, allow_rejection: true });
  expect(rejected.rejected).toBe('GroupingConflict');
  const linked = await scopeProbe.pg<{ id: string }>(
    'SELECT id FROM tracer_error_cluster_traces WHERE cluster_id=$1 AND trace_id=$2 AND deleted=false',
    [first.issue.id, later.trace_id]);
  expect(linked).toEqual([]);
  expect((await storedIssue(scopeProbe, first.issue)).status).toBe('for_review');
});
