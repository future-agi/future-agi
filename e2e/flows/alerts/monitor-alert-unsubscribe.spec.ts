import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { mintUnsubscribeLink } from '../../lib/email-link-seed';

// UserAlertMonitorSerializer and ProjectSerializer define these wire bodies;
// UserAlertMonitorView.delete takes { ids }.
const MONITORS = '/tracer/user-alerts/';
const PROJECTS = '/tracer/project/';
// AlertsSheetView.jsx exposes Edit Rule; AlertSettingsForm.jsx the name field and Update button.
const ALERTS = '/dashboard/alerts';
// futureagi/tfc/utils/email_unsubscribe.py: MONITOR_ALERTS links carry the monitor id as scope.
const CATEGORY = 'monitor_alerts';
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

test('ALERT-E2E-004: an alert recipient unsubscribes from one monitor and an open editor\'s save does not add them back', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ALERT-E2E-004', area: 'alerts',
    userGoal: 'An on-call address stops one monitor\'s alert emails from the link in an alert, and stays off when the alert owner later saves an editor they opened before the unsubscribe.',
    steps: [
      'create a muted monitor that emails the owner and an on-call address',
      'open the monitor in the alert editor',
      'open the on-call address\'s unsubscribe link in another tab and see the monitor named',
      'press Unsubscribe and see Unsubscribed with no Resubscribe button',
      'rename the monitor in the editor opened before the unsubscribe and save it',
      'the owner, the last recipient, unsubscribes from the same monitor',
      'reopen the editor and save a rename of the monitor left with no recipients',
      'delete the monitor and open the link again',
    ],
    backendChecks: [
      'opening the link alone leaves both addresses on the monitor in PG tracer_useralertmonitor.notification_emails',
      'Unsubscribe removes only the on-call address from the monitor in PG',
      'the editor\'s save renames the monitor in PG and keeps the on-call address removed',
      'the last recipient\'s Unsubscribe leaves the monitor with no recipients in PG',
      'the editor saves a rename of the monitor with no recipients, and the list stays empty in PG',
      'after the monitor is deleted the link says Monitor deleted and the deleted row\'s recipients are unchanged',
    ],
  }),
}, async ({ page, context, actor, probe }, testInfo) => {
  test.setTimeout(720_000); // ten UI_READY steps plus seeding.
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-alert-unsub-${suffix}`;
  const renamed = `${name}-renamed`;
  const renamedAgain = `${name}-renamed-again`;
  const oncall = `e2e-oncall-${suffix}@example.com`;
  const project = await actor.api.post<{ result: { project_id: string } }>(PROJECTS, {
    name, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  await actor.api.post(MONITORS, {
    project: project.result.project_id, organization: actor.organizationId, workspace: actor.workspaceId,
    name, metric_type: 'count_of_errors',
    threshold_operator: 'greater_than', threshold_type: 'static', critical_threshold_value: 999999,
    alert_frequency: 60, is_mute: true, slack_webhook_url: '', filters: {},
    notification_emails: [actor.email, oncall],
  });
  const [monitor] = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_useralertmonitor WHERE name = $1 AND organization_id = $2 AND deleted = false',
    [name, actor.organizationId]);
  const link = mintUnsubscribeLink({ email: oncall, category: CATEGORY, scope: monitor.id });
  const ownerLink = mintUnsubscribeLink({ email: actor.email, category: CATEGORY, scope: monitor.id });
  await testInfo.attach('seeded', {
    body: JSON.stringify({ monitorId: monitor.id, name, oncall, owner: actor.email, link, ownerLink }, null, 2),
    contentType: 'application/json',
  });
  const row = async () => (await probe.pg<{ name: string; r: string[]; deleted: boolean }>(
    'SELECT name, notification_emails AS r, deleted FROM tracer_useralertmonitor WHERE id = $1', [monitor.id]))[0];

  const openEditor = async (current: string) => {
    await page.goto(ALERTS);
    await page.getByPlaceholder('Search').first().fill(current, { timeout: UI_READY });
    const listed = page.locator(`[data-alert-row-id="${monitor.id}"][data-alert-row-name="${current}"]`);
    await listed.click({ timeout: UI_READY });
    await page.getByRole('button', { name: 'Edit Rule', exact: true }).click({ timeout: UI_READY });
    await expect(page.getByPlaceholder('Enter alert name', { exact: true })).toHaveValue(current, { timeout: UI_READY });
  };
  const saveEditor = async () => {
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname === `${MONITORS}${monitor.id}/`
      && ['PUT', 'PATCH'].includes(r.request().method()), { timeout: UI_READY });
    await page.getByRole('button', { name: /Update.*Alert/i }).click();
    return saved;
  };

  await test.step('open the monitor in the alert editor', async () => {
    await openEditor(name);
  }, { timeout: UI_READY });

  const tab = await context.newPage();
  await test.step('open the on-call unsubscribe link: it names the monitor and only asks', async () => {
    await tab.goto(link);
    await expect(tab.getByRole('heading')).toHaveText(['Unsubscribe?'], { timeout: UI_READY });
    await expect(tab.locator('main p')).toHaveText(
      `Stop sending alert emails for the monitor “${name}” to ${oncall}?`);
    await expect(tab.getByRole('button')).toHaveText(['Unsubscribe']);
    expect((await row()).r).toEqual([actor.email, oncall]);
  }, { timeout: UI_READY });

  await test.step('Unsubscribe removes only that address', async () => {
    await tab.getByRole('button', { name: 'Unsubscribe' }).click();
    await expect(tab.getByRole('heading')).toHaveText(['Unsubscribed'], { timeout: UI_READY });
    await expect(tab.locator('main p')).toHaveText(
      `${oncall} will no longer receive alert emails for the monitor “${name}”. `
      + `You'll get these alerts again if the monitor's owner, or anyone who can edit this monitor, adds ${oncall} back to its recipients.`);
    await expect(tab.getByRole('button')).toHaveCount(0);
    expect((await row()).r).toEqual([actor.email]);
  }, { timeout: UI_READY });

  await test.step('save the editor opened before the unsubscribe', async () => {
    await page.getByPlaceholder('Enter alert name', { exact: true }).fill(renamed);
    const response = await saveEditor();
    await testInfo.attach('editor-save-body', {
      body: response.request().postData() ?? '', contentType: 'application/json',
    });
    expect(response.status()).toBe(200);
    expect(await row()).toEqual({ name: renamed, r: [actor.email], deleted: false });
  }, { timeout: UI_READY });

  await test.step('the last recipient unsubscribes too', async () => {
    await tab.goto(ownerLink);
    await expect(tab.getByRole('heading')).toHaveText(['Unsubscribe?'], { timeout: UI_READY });
    await tab.getByRole('button', { name: 'Unsubscribe' }).click();
    await expect(tab.getByRole('heading')).toHaveText(['Unsubscribed'], { timeout: UI_READY });
    expect((await row()).r).toEqual([]);
  }, { timeout: UI_READY });

  await test.step('reopen the editor: a monitor with no recipients still saves a rename', async () => {
    await openEditor(renamed);
    await page.getByPlaceholder('Enter alert name', { exact: true }).fill(renamedAgain);
    const response = await saveEditor();
    expect(response.status()).toBe(200);
    expect(await row()).toEqual({ name: renamedAgain, r: [], deleted: false });
  }, { timeout: UI_READY * 2 });

  await test.step('a deleted monitor\'s link says so', async () => {
    const deleted = await actor.api.request('delete', MONITORS, { data: { ids: [monitor.id] } });
    expect(deleted.status).toBe(200);
    await tab.goto(link);
    await expect(tab.getByRole('heading')).toHaveText(['Monitor deleted'], { timeout: UI_READY });
    await expect(tab.getByRole('button')).toHaveCount(0);
    expect(await row()).toEqual({ name: renamedAgain, r: [], deleted: true });
  }, { timeout: UI_READY });
});
