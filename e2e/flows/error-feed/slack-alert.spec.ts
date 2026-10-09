import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/api/errorFeed/alerts.js and tracer/urls.py.
const ALERTS_PATH = '/tracer/alerts/';
const ISSUE_SEED_PATH = '/tracer/e2e/feed-issue/';
// Pinned from e2e/stack/mock-slack/server.mjs.
const CHANNEL_NAME = '#e2e-alerts';
const UI_READY = 60_000;

interface Rule { id: string; name: string; slack_channel_id: string }
interface AlertList { result: { alerts: Rule[] } }
interface IssueSeed { issue_id: string; cluster_id: string }
interface MockMessage { text: string; channel: string; ts: string }

test('ERR-E2E-001: connect Slack and notify only for matching Error Feed issues', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ERR-E2E-001', area: 'error-feed',
    userGoal: 'A developer connects Slack, creates an Error Feed rule, and receives only matching issue notifications',
    steps: [
      'open the Error Feed alert builder and enter a project, trigger, and filters',
      'connect a Slack workspace from the builder and return to the saved draft',
      'select a channel and create the rule',
      'create a nonmatching issue and a matching issue',
      'see the matching notification in Slack',
    ],
    backendChecks: [
      'the rule is saved in the scoped alert API with the selected Slack channel',
      'both seeded issue events are processed and only the matching issue has a sent delivery',
      'the mock Slack API receives exactly the matching issue message with a Feed link',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(360_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-err1-${suffix}`;
  const ruleName = `e2e-rule-${suffix}`;
  const matchingTitle = `e2e-matching-${suffix}`;
  const nonmatchingTitle = `e2e-nonmatching-${suffix}`;
  const seededTrace = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName, rootName: `e2e.root-${suffix}`,
  });
  await testInfo.attach('seeded-trace', { body: JSON.stringify(seededTrace), contentType: 'application/json' });
  await expect.poll(async () => {
    const rows = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId],
    );
    return rows[0]?.id;
  }, POLL.SPAN_VISIBLE).not.toBeUndefined();
  // Assertion above polls readiness; this read gives the typed ID for the UI and seeder.
  const project = (await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, actor.organizationId],
  ))[0];
  expect(project.id).toBeTruthy();

  await test.step('UI: fill the alert and connect Slack without losing the draft', async () => {
    await page.goto('/dashboard/error-feed/alerts', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Create alert' }).first().click();
    await page.getByRole('textbox', { name: 'Rule name' }).fill(ruleName);
    await page.getByRole('combobox', { name: 'Project' }).click();
    await page.getByRole('option', { name: projectName }).click();
    await page.getByRole('combobox', { name: 'Sources' }).click();
    await page.getByRole('option', { name: 'Scanner' }).click();
    await page.keyboard.press('Escape');
    await page.getByRole('combobox', { name: 'Severity filter' }).click();
    await page.getByRole('option', { name: 'High' }).click();
    await page.keyboard.press('Escape');
    await page.getByRole('combobox', { name: 'Issue categories' }).fill('Timeout');
    await page.getByRole('combobox', { name: 'Issue categories' }).press('Enter');
    await page.getByRole('button', { name: 'Connect Slack' }).click();
    await expect(page).toHaveURL(/resume_alert=1/, { timeout: UI_READY });
    await expect(page.getByRole('textbox', { name: 'Rule name' })).toHaveValue(ruleName);
    await expect(page.getByRole('combobox', { name: 'Project' })).toHaveText(projectName);
  });

  await test.step('UI and API: select a channel and save the scoped rule', async () => {
    await page.getByRole('combobox', { name: 'Slack workspace' }).click();
    await page.getByRole('option', { name: 'E2E Slack Workspace' }).click();
    await page.getByRole('combobox', { name: 'Channel' }).fill('e2e-alerts');
    await page.getByRole('option', { name: CHANNEL_NAME }).click();
    await page.getByRole('button', { name: 'Create alert' }).last().click();
    await expect(page.getByText(ruleName, { exact: true })).toBeVisible({ timeout: UI_READY });
    const list = await actor.api.get<AlertList>(ALERTS_PATH, { kind: 'error_feed' });
    const rule = list.result.alerts.find((item) => item.name === ruleName);
    expect(rule).toBeDefined();
    expect(rule?.slack_channel_id).toBe('CE2EALERTS');
    await testInfo.attach('saved-alert-rule', { body: JSON.stringify(rule), contentType: 'application/json' });
  });

  const nonmatching = await actor.api.post<IssueSeed>(ISSUE_SEED_PATH, {
    project_id: project.id, title: nonmatchingTitle, source: 'eval', severity: 'high',
    issue_category: 'Timeout', status: 'for_review', occurrences: 1,
  });
  const matching = await actor.api.post<IssueSeed>(ISSUE_SEED_PATH, {
    project_id: project.id, title: matchingTitle, source: 'scanner', severity: 'high',
    issue_category: 'Timeout', status: 'for_review', occurrences: 1,
  });
  await testInfo.attach('seeded-issues', {
    body: JSON.stringify({ nonmatching, matching }), contentType: 'application/json',
  });

  await test.step('storage and Slack: only the matching issue delivers', async () => {
    await expect.poll(async () => {
      const rows = await probe.pg<{ processed: number }>(
        `SELECT count(*)::int AS processed FROM tracer_error_feed_issue_event
         WHERE cluster_id IN ($1, $2) AND processed_at IS NOT NULL`,
        [nonmatching.issue_id, matching.issue_id],
      );
      return rows[0].processed;
    }, POLL.ASYNC_JOB).toBe(2);
    await expect.poll(async () => {
      const rows = await probe.pg<{ status: string }>(
        `SELECT d.status FROM tracer_error_feed_alert_delivery d
         WHERE d.cluster_id = $1`, [matching.issue_id],
      );
      return rows.map((row) => row.status);
    }, POLL.ASYNC_JOB).toEqual(['sent']);
    const nonmatchingDeliveries = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_error_feed_alert_delivery WHERE cluster_id = $1',
      [nonmatching.issue_id],
    );
    expect(nonmatchingDeliveries).toEqual([]);
    const response = await req.get(`${E2E.slackMockUrl}/messages`);
    const body = await response.json() as { messages: MockMessage[] };
    const matches = body.messages.filter((message) => message.text.includes(matchingTitle));
    expect(matches).toHaveLength(1);
    expect(matches[0].channel).toBe('CE2EALERTS');
    expect(JSON.stringify(matches[0])).toContain(matching.cluster_id);
    expect(body.messages.filter((message) => message.text.includes(nonmatchingTitle))).toEqual([]);
  });
  await req.dispose();
});
