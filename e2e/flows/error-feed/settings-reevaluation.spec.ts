import { test, expect } from '../../lib/error-feed-fixtures';
import { ErrorFeedHarness } from '../../lib/error-feed-harness';
import { flowAnnotation } from '../../lib/flow-meta';
import { chooseStatus, expectFeedStatus, openIssue, storedIssue, expectUiStatus } from '../../lib/error-feed-ui';
import { LABELS, openSettings, readSettings, saveSettings } from '../../lib/error-feed-settings';

test('ESC-E2E-006: Apply saved thresholds without arrivals and preserve manual choices', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'ESC-E2E-006', area: 'error-feed',
    userGoal: 'An administrator changes thresholds and automatic decisions update after fresh confirmation',
    steps: ['prepare a system escalation, candidate, review issue and manually controlled issue',
      'save workspace thresholds', 'publish no new findings', 'advance four eligible boundaries',
      'inspect policy revision, frozen incident baseline and preserved manual choice'],
    backendChecks: ['PG settings save does not itself transition any issue',
      'new policy revision resets candidate confirmation and preserves the active baseline after history ages out',
      'only qualifying system-owned issues change after full confirmation; API and UI show those exact decisions'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  test.setTimeout(1_200_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  // History expires between old-policy confirmation and the settings change.
  // One initial cohort respects the existing F6 full-membership candidate bound.
  const activeProject = await harness.project('settings-active', 48, -1480);
  const growth = await harness.ingest(activeProject.name, 20, harness.at(-20));
  const active = (await harness.publish(activeProject,
    { traces: [...activeProject.traces, growth] })).issue!;
  for (const minute of [10, 15, 20, 25]) await harness.evaluate(activeProject, minute);
  const reviewProject = await harness.project('settings-review', 10);
  const review = (await harness.publish(reviewProject, { now: 26 })).issue!;
  const candidateProject = await harness.project('settings-candidate', 20);
  const candidate = (await harness.publish(candidateProject, { now: 26 })).issue!;
  const manualProject = await harness.project('settings-manual', 20);
  const manual = (await harness.publish(manualProject, { now: 26 })).issue!;
  const manualView = await openIssue(browser, scopeActors, manual);
  await chooseStatus(manualView.page, manual, 'for_review');
  await manualView.context.close();
  await harness.evaluate(activeProject, 30);
  expect((await storedIssue(scopeProbe, active)).status).toBe('escalating');
  expect((await storedIssue(scopeProbe, candidate)).escalation_state.progress!.entry_checks).toBe(1);
  expect((await storedIssue(scopeProbe, review)).escalation_state.progress!.entry_checks).toBe(0);
  const before = await readSettings(scopeActors, scopeActors.ownerA);
  const settingsView = await openSettings(browser, scopeActors, scopeActors.ownerA);
  await settingsView.dialog.getByRole('spinbutton', { name: LABELS.min_findings }).fill('10');
  await settingsView.dialog.getByRole('spinbutton', { name: LABELS.entry_delta }).fill('8');
  await saveSettings(settingsView.page, settingsView.dialog);
  expect((await readSettings(scopeActors, scopeActors.ownerA)).revision).toBe(before.revision + 1);
  expect((await storedIssue(scopeProbe, active)).status).toBe('escalating');
  expect((await storedIssue(scopeProbe, review)).status).toBe('for_review');
  expect((await storedIssue(scopeProbe, candidate)).status).toBe('for_review');
  for (const minute of [35, 40, 45]) {
    await harness.evaluate(activeProject, minute);
    expect((await storedIssue(scopeProbe, review)).status).toBe('for_review');
    expect((await storedIssue(scopeProbe, candidate)).status).toBe('for_review');
  }
  await harness.evaluate(activeProject, 50);
  const activeSaved = await storedIssue(scopeProbe, active);
  expect(activeSaved.status).toBe('escalating');
  expect(Number(activeSaved.status_revision)).toBe(1);
  expect(activeSaved.escalation_state.progress!.frozen_baseline).toBe('2');
  expect(activeSaved.escalation_state.reason).toMatchObject({ historical_count: 0, baseline: 2,
    policy_revision: before.revision + 1 });
  for (const item of [{ project: reviewProject, issue: review }, { project: candidateProject, issue: candidate }]) {
    const saved = await storedIssue(scopeProbe, item.issue);
    expect(saved.status).toBe('escalating');
    expect(Number(saved.status_revision)).toBe(1);
    expect(saved.escalation_state.reason!.policy_revision).toBe(before.revision + 1);
    const row = await expectFeedStatus(scopeActors, item.project, item.issue, 'escalating');
    expect(row.escalation!.policy_revision).toBe(before.revision + 1);
    const view = await openIssue(browser, scopeActors, item.issue);
    await expectUiStatus(view.page, 'escalating');
    await view.context.close();
  }
  const chosen = await storedIssue(scopeProbe, manual);
  expect(chosen.status).toBe('for_review');
  expect(chosen.status_origin).toBe('manual');
  expect(Number(chosen.status_revision)).toBe(1);
  await expectFeedStatus(scopeActors, manualProject, manual, 'for_review');
});
