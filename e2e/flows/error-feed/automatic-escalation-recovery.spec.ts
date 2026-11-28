import { test, expect } from '../../lib/error-feed-fixtures';
import { ErrorFeedHarness } from '../../lib/error-feed-harness';
import { flowAnnotation } from '../../lib/flow-meta';
import { expectFeedStatus, openIssue, expectUiStatus, storedIssue, STATS_PATH } from '../../lib/error-feed-ui';
import { UI_READY } from '../../lib/error-feed-settings';

test('ESC-E2E-002: Confirm sustained growth and recover a quiet issue without new findings', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'ESC-E2E-002', area: 'error-feed',
    userGoal: 'An engineer sees sustained growth escalate and a quiet issue return to review',
    steps: ['publish baseline findings, then attach a burst through collector and fenced publishers',
      'inspect review before confirmation', 'advance four normal boundaries through Temporal',
      'inspect escalation and evidence', 'publish nothing further and advance quiet recovery', 'inspect review'],
    backendChecks: ['PG has exactly one status revision at entry and one at recovery, including duplicate checks',
      'frozen baseline, counts, thresholds and boundary timestamps equal the independently seeded windows',
      'detail/list/status-filter/statistics APIs and UI agree with each persisted transition'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  // Provisioning + 12 production Temporal completions + browser first paint.
  test.setTimeout(1_080_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  // Keep the initial candidate below F6's existing 16-member bound, then
  // attach a real 20-finding burst through the fenced publisher.
  const project = await harness.project('growth-and-quiet', 12, -120);
  const first = await harness.publish(project);
  const issue = first.issue!;
  const current = await harness.ingest(project.name, 20, harness.at(-20));
  const attachment = await harness.publish(project,
    { target_issue_id: issue.id, traces: [current], now: 2 });
  expect(attachment.issue!.id).toBe(issue.id);
  expect(attachment.issue!.status).toBe('for_review');
  const { page } = await openIssue(browser, scopeActors, issue);
  await expectUiStatus(page, 'for_review');
  for (const minute of [10, 15, 20]) {
    await harness.evaluate(project, minute);
    const state = await storedIssue(scopeProbe, issue);
    expect(state.status).toBe('for_review');
    expect(Number(state.status_revision)).toBe(0);
  }
  await harness.evaluate(project, 25);
  await harness.evaluate(project, 25); // A duplicate wake is not another observation.
  let saved = await storedIssue(scopeProbe, issue);
  expect(saved.status).toBe('escalating');
  expect(Number(saved.status_revision)).toBe(1);
  expect(saved.escalation_state.reason).toMatchObject({ current_count: 20, historical_count: 12,
    baseline: 0.5, entry_threshold: 20, policy_revision: 0 });
  expect(new Date(saved.escalation_state.reason!.window_end as string).toISOString()).toBe(harness.at(15));
  expect(saved.escalation_state.progress!.frozen_baseline).toBe('1/2');
  let row = await expectFeedStatus(scopeActors, project, issue, 'escalating');
  expect(row.escalation).toMatchObject({ current_count: 20, affected_trace_count: 1, baseline: 0.5, entry_threshold: 20 });
  await page.reload();
  await expectUiStatus(page, 'escalating');
  await expect(page.getByText(/20 findings across 1 traces/)).toBeVisible({ timeout: UI_READY });
  const stats = await scopeActors.send<{ result: { escalating: number; for_review: number } }>(
    scopeActors.ownerA, 'GET', STATS_PATH, undefined, { project_id: project.id, time_range_days: 90 });
  expect(stats.status).toBe(200);
  expect(stats.body.result).toMatchObject({ escalating: 1, for_review: 0 });
  for (const minute of [30, 35, 40, 45, 50, 55, 60, 65]) await harness.evaluate(project, minute);
  saved = await storedIssue(scopeProbe, issue);
  expect(saved.status).toBe('for_review');
  expect(Number(saved.status_revision)).toBe(2);
  expect(saved.escalation_state.reason).toMatchObject({ current_count: 0, baseline: 0.5,
    transition_reason: 'confirmed_recovery' });
  expect(new Date(saved.escalation_state.reason!.window_end as string).toISOString()).toBe(harness.at(55));
  row = await expectFeedStatus(scopeActors, project, issue, 'for_review');
  expect(row.escalation!.current_count).toBe(0);
  await page.reload();
  await expectUiStatus(page, 'for_review');
});
