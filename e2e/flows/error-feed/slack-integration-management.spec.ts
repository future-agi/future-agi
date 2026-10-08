import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/api/integrations/index.js and integrations/urls.py.
const INSTALL_PATH = '/integrations/slack/install/';
const CONNECTIONS_PATH = '/integrations/connections/';
const UI_READY = 60_000;

interface Install { result: { authorization_url: string } }
interface Connection { id: string; platform: string; display_name: string }
interface ConnectionList { result: { connections: Connection[] } }

test('ERR-E2E-003: failed Slack reconnect can recover and the connection can be removed', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ERR-E2E-003', area: 'error-feed',
    userGoal: 'A developer can recover from a denied Slack reconnect and disconnect the integration',
    steps: [
      'connect Slack and open its card in Settings Integrations',
      'attempt to reconnect but deny consent and see the error',
      'retry the reconnect successfully',
      'disconnect Slack from the integration card',
    ],
    backendChecks: [
      'denied consent leaves the existing connection active and unchanged',
      'successful reconnect preserves the same workspace connection ID',
      'disconnect removes the connection from the scoped integrations API',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const install = await actor.api.post<Install>(INSTALL_PATH, {});
  const callback = await req.get(install.result.authorization_url);
  expect(callback.ok()).toBeTruthy();
  const list = await actor.api.get<ConnectionList>(CONNECTIONS_PATH);
  const original = list.result.connections.find((connection) => connection.platform === 'slack');
  expect(original).toBeDefined();
  await testInfo.attach('slack-connection', { body: JSON.stringify(original), contentType: 'application/json' });

  await page.goto('/dashboard/settings/integrations', { waitUntil: 'domcontentloaded' });
  await page.getByText('E2E Slack Workspace', { exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Manage E2E Slack Workspace' })).toBeVisible({ timeout: UI_READY });
  await page.route(`${E2E.slackMockUrl}/oauth/v2/authorize**`, async (route) => {
    await route.continue({ url: `${route.request().url()}&deny=1` });
  });
  await page.getByRole('button', { name: 'Reconnect Slack' }).click();
  await expect(page).toHaveURL(/slack=error/, { timeout: UI_READY });
  await expect(page.getByText('Could not connect Slack. Please try again.')).toBeVisible({ timeout: UI_READY });
  const afterDenied = await actor.api.get<ConnectionList>(CONNECTIONS_PATH);
  expect(afterDenied.result.connections.find((connection) => connection.platform === 'slack')?.id).toBe(original!.id);

  await page.unroute(`${E2E.slackMockUrl}/oauth/v2/authorize**`);
  await page.getByText('E2E Slack Workspace', { exact: true }).click();
  await page.getByRole('button', { name: 'Reconnect Slack' }).click();
  await expect(page).toHaveURL(/slack=connected/, { timeout: UI_READY });
  const afterReconnect = await actor.api.get<ConnectionList>(CONNECTIONS_PATH);
  expect(afterReconnect.result.connections.find((connection) => connection.platform === 'slack')?.id).toBe(original!.id);

  await page.getByText('E2E Slack Workspace', { exact: true }).click();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.getByText('E2E Slack Workspace', { exact: true })).toHaveCount(0, { timeout: UI_READY });
  const afterDisconnect = await actor.api.get<ConnectionList>(CONNECTIONS_PATH);
  expect(afterDisconnect.result.connections.filter((connection) => connection.platform === 'slack')).toEqual([]);
  await req.dispose();
});
