import { test, expect } from '../../lib/error-feed-fixtures';
import { ErrorFeedHarness, IssueReceipt } from '../../lib/error-feed-harness';
import { flowAnnotation } from '../../lib/flow-meta';
import { storedIssue, listIssues, openIssue, expectUiStatus } from '../../lib/error-feed-ui';
import { SETTINGS_PATH } from '../../lib/error-feed-settings';

test('ESC-E2E-007: Evaluate current findings after merge, split, removal and supersession', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'ESC-E2E-007', area: 'error-feed',
    userGoal: 'An engineer sees decisions follow current evidence when issues are regrouped or reinvestigated',
    steps: ['publish and confirm two small issues with a real workspace policy',
      'merge through fenced reconciliation', 'confirm the surviving issue', 'split and remove through reconciliation',
      'replace a source report', 'inspect surviving issues and original occurrence times'],
    backendChecks: ['PG current memberships match exact source finding IDs and keep original timestamps',
      'structural changes request fresh confirmation and retired issues stop evaluation',
      'source supersession removes obsolete findings; list APIs and UI contain only current surviving issues'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  test.setTimeout(1_440_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  const settings = await scopeActors.send(scopeActors.ownerA, 'PATCH', SETTINGS_PATH,
    { revision: 0, min_findings: 2, entry_delta: 1, recovery_min: 1,
      recovery_delta: 0, recovery_multiplier: 1 });
  expect(settings.status).toBe(200); // Durations remain the normal 15/10 minutes.
  const project = await harness.project('membership', 4, 0);
  const first = (await harness.publish(project, { title: 'Refund argument mismatch' })).issue!;
  const peerTrace = await harness.ingest(project.name, 4, harness.at(0));
  const second = (await harness.publish(project, { traces: [peerTrace], title: 'Refund execution mismatch', now: 2 })).issue!;
  for (const minute of [15, 20, 25, 30]) await harness.evaluate(project, minute);
  expect((await storedIssue(scopeProbe, first)).status).toBe('escalating');
  expect((await storedIssue(scopeProbe, second)).status).toBe('escalating');
  const original = await scopeProbe.pg<{ finding_id: string; occurred_at: Date }>(
    'SELECT finding_id, occurred_at FROM tracer_error_cluster_traces WHERE cluster_id=ANY($1::uuid[]) AND deleted=false',
    [[first.id, second.id]]);
  expect(original).toHaveLength(8);
  const originalIds = original.map(row => row.finding_id).sort();
  const trigger = async (minute: number) => {
    const trace = await harness.ingest(project.name, 1, harness.at(0));
    await harness.publish(project, { traces: [trace], now: minute, prepare_only: true });
  };
  await trigger(31);
  const merge = await harness.reconcile(project, 'merge', [first.id, second.id], 33);
  expect(merge.issues).toHaveLength(1);
  const merged = merge.issues[0];
  expect(merged.status).toBe('for_review');
  const retiredBefore = [await storedIssue(scopeProbe, first), await storedIssue(scopeProbe, second)];
  for (const retired of retiredBefore) expect(retired.deleted).toBe(true);
  for (const minute of [40, 45, 50, 55]) await harness.evaluate(project, minute);
  const active = await storedIssue(scopeProbe, merged);
  expect(active.status).toBe('escalating');
  expect(active.escalation_state.reason!.current_count).toBe(8);
  const mergedRows = await scopeProbe.pg<{ finding_id: string; occurred_at: Date }>(
    'SELECT finding_id, occurred_at FROM tracer_error_cluster_traces WHERE cluster_id=$1 AND deleted=false', [merged.id]);
  expect(mergedRows.map(row => row.finding_id).sort()).toEqual(originalIds);
  expect(mergedRows.every(row => row.occurred_at.toISOString() === harness.at(0))).toBe(true);
  for (const [index, old] of [first, second].entries()) {
    const state = await storedIssue(scopeProbe, old);
    expect(state.escalation_state.reason).toEqual(retiredBefore[index].escalation_state.reason);
  }
  await trigger(56);
  const split = await harness.reconcile(project, 'split', [merged.id], 58);
  expect(split.issues).toHaveLength(2);
  for (const issue of split.issues) expect(issue.status).toBe('for_review');
  await harness.evaluate(project, 65);
  for (const issue of split.issues) {
    const state = await storedIssue(scopeProbe, issue);
    expect(state.escalation_state.reason!.current_count).toBe(4);
    expect(state.escalation_state.reason!.transition_reason).toBe('membership_changed');
  }
  await trigger(66);
  const removal = await harness.reconcile(project, 'remove', [split.issues[0].id], 68);
  expect(removal.removed_ids).toHaveLength(1);
  const afterRemoval = await scopeProbe.pg<{ finding_id: string }>(
    'SELECT finding_id FROM tracer_error_cluster_traces WHERE cluster_id=$1 AND deleted=false', [split.issues[0].id]);
  expect(afterRemoval).toHaveLength(3);
  expect(afterRemoval.map(row => row.finding_id)).not.toContain(removal.removed_ids[0]);
  const beforeReplacement = await scopeProbe.pg<{ finding_id: string }>(
    `SELECT ect.finding_id FROM tracer_error_cluster_traces ect
     JOIN tracer_trace_investigation_finding finding ON finding.id=ect.finding_id
     JOIN tracer_trace_investigation_report report ON report.id=finding.report_id
     WHERE report.trace_id=$1 AND ect.deleted=false`, [project.traces[0].trace_id]);
  expect(beforeReplacement.length).toBeGreaterThan(0);
  await harness.publish(project, { traces: [{ ...project.traces[0], findings: 0 }], now: 69 });
  const oldCurrent = await scopeProbe.pg<{ finding_id: string }>(
    'SELECT finding_id FROM tracer_error_cluster_traces WHERE finding_id=ANY($1::uuid[]) AND deleted=false',
    [beforeReplacement.map(row => row.finding_id)]);
  expect(oldCurrent).toEqual([]);
  await harness.evaluate(project, 75);
  const visible = await listIssues(scopeActors, project);
  const remaining = await scopeProbe.pg<{ finding_id: string; cluster_id: string }>(
    `SELECT finding_id, cluster_id FROM tracer_error_cluster_traces
     WHERE cluster_id=ANY($1::uuid[]) AND deleted=false`, [split.issues.map(issue => issue.id)]);
  const expectedRemaining = originalIds.filter(id => id !== removal.removed_ids[0]
    && !beforeReplacement.some(row => row.finding_id === id));
  expect(remaining.map(row => row.finding_id).sort()).toEqual(expectedRemaining.sort());
  const expectedVisible = split.issues.filter(issue => remaining.some(row => row.cluster_id === issue.id));
  expect(visible.map(row => row.cluster_id).sort())
    .toEqual(expectedVisible.map(issue => issue.cluster_id).sort());
  expect(visible.map(row => row.cluster_id)).not.toContain(first.cluster_id);
  expect(visible.map(row => row.cluster_id)).not.toContain(second.cluster_id);
  expect(visible.map(row => row.cluster_id)).not.toContain(merged.cluster_id);
  const survivor = split.issues.find(issue => visible.some(row => row.cluster_id === issue.cluster_id)) as IssueReceipt;
  expect(survivor).toBeTruthy();
  const view = await openIssue(browser, scopeActors, survivor);
  await expectUiStatus(view.page, 'for_review');
});
