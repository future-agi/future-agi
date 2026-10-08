import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/pages/dashboard/error-feed/ErrorFeedAlerts.jsx and
// futureagi/tracer/views/alerts.py.
const ALERTS_PATH = '/tracer/alerts/';
const ISSUE_SEED_PATH = '/tracer/e2e/feed-issue/';
const SLACK_INSTALL_PATH = '/integrations/slack/install/';
const UI_READY = 60_000;

interface Install { result: { authorization_url: string } }
interface Rule { id: string; name: string; trigger_type: string; filters: { issue_groups: string[] } }
interface AlertList { result: { alerts: Rule[] } }
interface SeededIssue { issue_id: string; cluster_id: string }

const cases = [
  { trigger: 'new_issue', label: 'A new issue is created' },
  { trigger: 'severity_reached', label: 'Severity reaches a level' },
  { trigger: 'escalating', label: 'Issue status changes to escalating' },
  { trigger: 'occurrences_crossed', label: 'Occurrence count crosses a threshold' },
] as const;

test('ERR-E2E-002: every issue trigger sends only after its selected transition', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ERR-E2E-002', area: 'error-feed',
    userGoal: 'A developer chooses each issue trigger and receives a Slack alert when its condition occurs',
    steps: [
      'connect a test Slack workspace and create an Error Feed project',
      'create a rule in the UI for each issue trigger with a distinct issue-group filter',
      'create and update issues through the test-only issue API to exercise each transition',
      'confirm each selected transition posts exactly one message to Slack',
    ],
    backendChecks: [
      'the alert API persists all four selected triggers and issue-group filters',
      'each matching transition creates a sent delivery for its rule',
      'the mock Slack API receives one message for each matched issue',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(720_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-err2-${suffix}`;
  const trace = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: `e2e.root-${suffix}`,
  });
  await testInfo.attach('seeded-trace', { body: JSON.stringify(trace), contentType: 'application/json' });
  await expect.poll(async () => {
    const rows = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId],
    );
    return rows[0]?.id;
  }, POLL.SPAN_VISIBLE).not.toBeUndefined();
  const project = (await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, actor.organizationId],
  ))[0];

  // API seeding uses SlackInstallSerializer's empty body; the browser flow
  // under test here is the When/If/Then builder, not the OAuth consent page.
  const install = await actor.api.post<Install>(SLACK_INSTALL_PATH, {});
  const callback = await req.get(install.result.authorization_url);
  expect(callback.ok()).toBeTruthy();

  for (const [index, item] of cases.entries()) {
    const ruleName = `e2e-${item.trigger}-${suffix}`;
    const group = `E2EGroup${index}${suffix}`;
    const issueTitle = `e2e-${item.trigger}-issue-${suffix}`;
    await test.step(`UI: configure ${item.trigger}`, async () => {
      await page.goto('/dashboard/error-feed/alerts', { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Create alert' }).first().click();
      await page.getByRole('textbox', { name: 'Rule name' }).fill(ruleName);
      await page.getByRole('combobox', { name: 'Project' }).click();
      await page.getByRole('option', { name: projectName }).click();
      await page.getByRole('combobox', { name: 'Issue event' }).click();
      await page.getByRole('option', { name: item.label }).click();
      if (item.trigger === 'severity_reached') {
        await page.getByRole('combobox', { name: 'Severity', exact: true }).click();
        await page.getByRole('option', { name: 'High' }).click();
      }
      if (item.trigger === 'occurrences_crossed') {
        await page.getByRole('spinbutton', { name: 'Occurrence threshold' }).fill('5');
      }
      await page.getByRole('combobox', { name: 'Issue groups' }).fill(group);
      await page.getByRole('combobox', { name: 'Issue groups' }).press('Enter');
      await page.getByRole('combobox', { name: 'Slack workspace' }).click();
      await page.getByRole('option', { name: 'E2E Slack Workspace' }).click();
      await page.getByRole('combobox', { name: 'Channel' }).fill('e2e-alerts');
      await page.getByRole('option', { name: '#e2e-alerts' }).click();
      await page.getByRole('button', { name: 'Create alert' }).last().click();
      await expect(page.getByText(ruleName, { exact: true })).toBeVisible({ timeout: UI_READY });
    });

    const alerts = await actor.api.get<AlertList>(ALERTS_PATH, { kind: 'error_feed' });
    const saved = alerts.result.alerts.find((rule) => rule.name === ruleName);
    expect(saved?.trigger_type).toBe(item.trigger);
    expect(saved?.filters.issue_groups).toEqual([group]);
    await testInfo.attach(`rule-${item.trigger}`, { body: JSON.stringify(saved), contentType: 'application/json' });

    const initial = await actor.api.post<SeededIssue>(ISSUE_SEED_PATH, {
      project_id: project.id, title: issueTitle, issue_group: group,
      source: 'scanner', status: 'for_review', severity: 'low', occurrences: 1,
    });
    if (item.trigger === 'severity_reached') {
      await actor.api.post(ISSUE_SEED_PATH, { project_id: project.id, issue_id: initial.issue_id, severity: 'high' });
    } else if (item.trigger === 'escalating') {
      await actor.api.post(ISSUE_SEED_PATH, { project_id: project.id, issue_id: initial.issue_id, status: 'escalating' });
    } else if (item.trigger === 'occurrences_crossed') {
      await actor.api.post(ISSUE_SEED_PATH, { project_id: project.id, issue_id: initial.issue_id, occurrences: 5 });
    }
    await testInfo.attach(`issue-${item.trigger}`, { body: JSON.stringify(initial), contentType: 'application/json' });

    await test.step(`delivery: ${item.trigger}`, async () => {
      await expect.poll(async () => {
        const rows = await probe.pg<{ status: string }>(
          'SELECT status FROM tracer_error_feed_alert_delivery WHERE rule_id = $1 AND cluster_id = $2',
          [saved!.id, initial.issue_id],
        );
        return rows.map((row) => row.status);
      }, POLL.ASYNC_JOB).toEqual(['sent']);
      const response = await req.get(`${E2E.slackMockUrl}/messages`);
      const body = await response.json() as { messages: { text: string }[] };
      expect(body.messages.filter((message) => message.text.includes(issueTitle))).toHaveLength(1);
    });
  }
  await req.dispose();
});
