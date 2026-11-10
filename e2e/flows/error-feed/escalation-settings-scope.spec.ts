import { request } from '@playwright/test';
import { test, expect } from '../../lib/error-feed-fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { SETTINGS_PATH, LABELS, UI_READY, openSettings, reopenSettings, readSettings,
  numbers, saveSettings, expectStored } from '../../lib/error-feed-settings';

test('ESC-E2E-005: Share workspace settings across projects and enforce scope permissions', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ESC-E2E-005', area: 'error-feed',
    userGoal: 'An administrator configures one workspace while other workspaces and read-only users stay isolated',
    steps: ['ingest two uniquely named projects in workspace A', 'save A settings',
      'inspect settings under each project filter', 'inspect sibling and foreign workspace defaults',
      'inspect member and viewer read-only controls', 'attempt forbidden and foreign-scope saves'],
    backendChecks: ['collector traces and PG projects belong to workspace A',
      'PG and scoped settings API contain one saved A policy and unchanged defaults in other workspaces',
      'unauthorized PATCHes return 403 and do not change policy values or revision'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Actor provisioning + two collector waits + ten first-paint actions.
  test.setTimeout(900_000);
  const actor = scopeActors.ownerA;
  const initial = await readSettings(scopeActors, actor);
  const defaults = numbers(initial.defaults);
  const custom = { ...defaults, min_findings: 35 };
  const req = await request.newContext();
  try {
    const projects = [];
    for (const suffix of ['one', 'two']) {
      const projectName = `${scopeActors.prefix}-settings-${suffix}`;
      const owner = scopeActors.owners[0];
      const seed = await sendTrace(req, { collectorUrl: E2E.collectorUrl,
        apiKey: owner.apiKey, secretKey: owner.secretKey, projectName });
      await testInfo.attach(`seed-${suffix}`, { contentType: 'application/json', body: JSON.stringify(seed) });
      await expect.poll(async () => {
        const rows = await scopeProbe.ch<{ n: string }>(
          'SELECT count() AS n FROM spans FINAL WHERE trace_id = {trace:String}', { trace: seed.traceId });
        return Number(rows[0].n);
      }, POLL.SPAN_VISIBLE).toBe(seed.spanIds.length);
      const rows = await scopeProbe.pg<{ id: string; workspace_id: string }>(
        'SELECT id, workspace_id FROM tracer_project WHERE name = $1 AND organization_id = $2',
        [projectName, actor.organizationId]);
      expect(rows).toHaveLength(1);
      expect(rows[0].workspace_id).toBe(actor.workspaceId);
      projects.push(projectName);
    }
    const { page, dialog } = await openSettings(browser, scopeActors, actor);
    await dialog.getByRole('spinbutton', { name: LABELS.min_findings }).fill('35');
    await saveSettings(page, dialog);
    await expectStored(scopeProbe, actor, custom, initial.revision + 1);
    for (const projectName of projects) {
      const selector = page.getByRole('combobox').filter({ hasText: /All Projects|settings-(one|two)/ });
      await selector.click();
      await page.getByRole('option', { name: projectName, exact: true }).click();
      const scopedDialog = await reopenSettings(page);
      await expect(scopedDialog.getByRole('spinbutton', { name: LABELS.min_findings }))
        .toHaveValue('35', { timeout: UI_READY });
      await scopedDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
      expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(custom);
    }
    for (const isolated of [scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id), scopeActors.ownerB]) {
      const policy = await readSettings(scopeActors, isolated);
      expect(numbers(policy.values)).toEqual(defaults);
      const other = await openSettings(browser, scopeActors, isolated);
      await expect(other.dialog.getByRole('spinbutton', { name: LABELS.min_findings })).toHaveValue('20');
      const stored = await scopeProbe.pg<{ min_findings: number; revision: string }>(
        'SELECT min_findings, revision FROM tracer_feed_escalation_policy WHERE workspace_id = $1',
        [isolated.workspaceId]);
      expect(stored).toHaveLength(1);
      expect(stored[0].min_findings).toBe(20);
      expect(Number(stored[0].revision)).toBe(0);
    }
    for (const readOnly of [scopeActors.member, scopeActors.viewer]) {
      const policy = await readSettings(scopeActors, readOnly);
      expect(policy.can_edit).toBe(false);
      expect(numbers(policy.values)).toEqual(custom);
      const view = await openSettings(browser, scopeActors, readOnly);
      await expect(view.dialog.getByRole('spinbutton', { name: LABELS.min_findings })).toBeDisabled();
      await expect(view.dialog.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0);
      const denied = await scopeActors.send(readOnly, 'PATCH', SETTINGS_PATH,
        { revision: policy.revision, min_findings: 99 });
      expect(denied.status).toBe(403);
    }
    const foreign = scopeActors.withWorkspace(scopeActors.ownerB, actor.workspaceId);
    // Existing workspace middleware rejects an org/workspace mismatch before
    // the settings view, using its established conflict response.
    expect((await scopeActors.send(foreign, 'PATCH', SETTINGS_PATH,
      { revision: initial.revision + 1, min_findings: 99 })).status).toBe(409);
    await expectStored(scopeProbe, actor, custom, initial.revision + 1);
    expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(custom);
  } finally {
    await req.dispose();
  }
});
