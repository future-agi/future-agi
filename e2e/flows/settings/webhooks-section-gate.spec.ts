import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import { flowAnnotation } from '../../lib/flow-meta';

const UI_READY = 60_000;
// Pinned from futureagi/integrations/urls.py (WH-3) and futureagi/webhooks/urls.py (WH-2).
const DESTINATIONS = '/integrations/destinations/';
const ENDPOINTS = '/webhooks/endpoints/';
// Pinned from accounts/serializers/contracts.py and views/workspace.py (as lib/scope-actors.ts creates one).
const WORKSPACES = '/accounts/workspaces/';
// What the stack under test is expected to be: "on" (the default) or "off" for a
// stack started with FI_WEBHOOKS_ENABLED=false (DESIGN.md section 17.2, WT-23). Section
// 18's release gate is passed (futureagi/webhooks/release_gate.py), so "on" needs no
// FI_WEBHOOKS_TEST_PLAN switch: it is the release configuration.
const EXPECTED = process.env.E2E_WEBHOOKS_SENDING ?? 'on';
const REASONS: Record<string, string[]> = { on: [], off: ['sending_off'] };

interface Access { available: boolean; reasons: string[]; can_view: boolean; workspace_id: string | null }
interface Envelope<T> { status: boolean; result: T }
interface EndpointRow { id: string; name: string; workspace_id: string | null }

test('SET-E2E-004: the Webhooks section exists exactly where this install can send webhooks', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-004', area: 'settings',
    userGoal: 'An admin sees the Webhooks section only on an install where webhooks work, never as "coming soon"',
    steps: [
      'open Settings > Integrations',
      "open the workspace's Settings > Integrations",
      "open another workspace's Settings > Integrations and add a webhook there",
      'open a webhook endpoint URL directly',
    ],
    backendChecks: [
      'GET /integrations/destinations/ reports webhooks available exactly when the stack sends (FI_WEBHOOKS_ENABLED) and the release gate is passed or the test plan runs (FI_WEBHOOKS_TEST_PLAN)',
      "the webhook added on another workspace's page belongs to that workspace (GET /webhooks/endpoints/), and the registry asked for that workspace lists it and not the session workspace's webhook",
    ],
    browserChecks: [
      'sending on: the Webhooks heading, Add webhook and the policy card are shown, on the org page and, with the workspace policy, on the workspace page',
      "sending on: another workspace's page lists that workspace's webhooks, not the session workspace's, and the org page does not list the new one",
      'sending off: no Webhooks heading, no Add webhook, no policy card, no "coming soon" on either page, and the endpoint URL returns to Integrations',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(300_000);
  expect(Object.keys(REASONS), `E2E_WEBHOOKS_SENDING=${EXPECTED}`).toContain(EXPECTED);
  const reg = await actor.api.get<{ result: { webhooks: Access } }>(DESTINATIONS);
  await testInfo.attach('webhooks-access', { contentType: 'application/json',
    body: JSON.stringify({ expected: EXPECTED, access: reg.result.webhooks, organizationId: actor.organizationId }) });
  expect(reg.result.webhooks.available).toBe(EXPECTED === 'on');
  expect(reg.result.webhooks.reasons).toEqual(REASONS[EXPECTED]);

  await test.step('UI: the Integrations page', async () => {
    const registryAnswer = page.waitForResponse((r) => r.url().includes(DESTINATIONS) && r.ok(), { timeout: UI_READY });
    await page.goto('/dashboard/settings/integrations', { waitUntil: 'domcontentloaded' });
    await registryAnswer;
    await expect(page.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    const heading = page.getByRole('heading', { name: 'Webhooks', exact: true });
    const policy = page.getByRole('region', { name: 'Webhook policy' });
    if (EXPECTED === 'on') {
      await expect(heading).toBeVisible({ timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Add webhook' })).toBeVisible();
      await expect(policy).toBeVisible({ timeout: UI_READY });
    } else {
      await expect(heading).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Add webhook' })).toHaveCount(0);
      await expect(policy).toHaveCount(0);
      await expect(page.getByText(/coming soon/i).filter({ hasText: /webhook/i })).toHaveCount(0);
    }
  });

  await test.step("UI: the workspace's Integrations page", async () => {
    await page.goto(`/dashboard/settings/workspace/${actor.workspaceId}/integrations`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    const heading = page.getByRole('heading', { name: 'Webhooks', exact: true });
    const policy = page.getByRole('region', { name: 'Webhook policy' });
    if (EXPECTED === 'on') {
      await expect(heading).toBeVisible({ timeout: UI_READY });
      await expect(policy.getByText('Webhook policy for this workspace', { exact: true })).toBeVisible({ timeout: UI_READY });
    } else {
      await expect(heading).toHaveCount(0);
      await expect(policy).toHaveCount(0);
      await expect(page.getByText(/coming soon/i).filter({ hasText: /webhook/i })).toHaveCount(0);
    }
  });

  if (EXPECTED === 'on') {
    // Review M6: the settings nav opens any of the person's workspaces while the session stays in its own. The
    // section must work in the page's workspace: list, add and policy.
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const aName = `e2e-ws-a-${suffix}`;
    const bName = `e2e-ws-b-${suffix}`;
    const made: string[] = [];
    try {
      await test.step("UI + API: another workspace's Integrations page works in that workspace", async () => {
        const ws = await actor.api.post<{ result: { workspace: { id: string; name: string } } }>(WORKSPACES,
          { name: `e2e-wh-ws-${suffix}` });
        const b = ws.result.workspace.id;
        const a = await actor.api.post<Envelope<{ endpoint: { id: string } }>>(ENDPOINTS, {
          name: aName, url: `https://hooks.acme.ai/${aName}`, workspace_id: actor.workspaceId,
          subscriptions: [{ event_type: 'prompt.version.created', filters: {} }],
        });
        made.push(a.result.endpoint.id);
        await testInfo.attach('seed-workspaces', { contentType: 'application/json',
          body: JSON.stringify({ organizationId: actor.organizationId, sessionWorkspace: actor.workspaceId,
            otherWorkspace: b, sessionEndpoint: a.result.endpoint.id }) });

        await page.goto(`/dashboard/settings/workspace/${b}/integrations`, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: 'Webhooks', exact: true })).toBeVisible({ timeout: UI_READY });
        const policy = page.getByRole('region', { name: 'Webhook policy' });
        await expect(policy.getByText('Webhook policy for this workspace', { exact: true })).toBeVisible({ timeout: UI_READY });
        await page.getByRole('button', { name: 'Add webhook' }).click();
        const dialog = page.getByRole('dialog', { name: 'Add webhook' });
        await expect(dialog.getByRole('radio', { name: 'This workspace' })).toBeChecked({ timeout: UI_READY });
        await dialog.getByRole('textbox', { name: 'Name' }).fill(bName);
        await dialog.getByRole('textbox', { name: 'Endpoint URL' }).fill(`https://hooks.acme.ai/${bName}`);
        await dialog.getByRole('checkbox', { name: 'A new version is committed' }).check();
        await dialog.getByRole('button', { name: 'Save' }).click();
        await page.getByRole('dialog', { name: `${bName} is ready` }).getByRole('button', { name: 'Done' })
          .click({ timeout: UI_READY });
        await expect(page.getByRole('row').filter({ hasText: bName })).toHaveCount(1, { timeout: UI_READY });
        await expect(page.getByRole('row').filter({ hasText: aName })).toHaveCount(0);

        const all = await actor.api.get<Envelope<{ results: EndpointRow[] }>>(ENDPOINTS);
        const added = all.result.results.filter((e) => e.name === bName);
        expect(added.map((e) => e.workspace_id)).toEqual([b]);
        made.push(added[0].id);
        const inB = await actor.api.withAuth(actor.tokens, actor.organizationId, b)
          .get<Envelope<{ destinations: { id: string }[]; webhooks: Access }>>(DESTINATIONS);
        expect(inB.result.webhooks.workspace_id).toBe(b);
        const ids = inB.result.destinations.map((d) => d.id);
        expect([ids.includes(added[0].id), ids.includes(a.result.endpoint.id)]).toEqual([true, false]);

        await page.goto('/dashboard/settings/integrations', { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('row').filter({ hasText: aName })).toHaveCount(1, { timeout: UI_READY });
        await expect(page.getByRole('row').filter({ hasText: bName })).toHaveCount(0);
      });
    } finally {
      // The worker's org is shared with the other webhooks flows: leave no endpoint behind.
      for (const id of made) {
        await actor.api.delete(`${ENDPOINTS}${id}/`).catch((e: unknown) => {
          if (!(e instanceof ApiError)) throw e;
        });
      }
    }
  }

  await test.step('UI: an endpoint URL', async () => {
    await page.goto('/dashboard/settings/integrations/webhooks/whep_00000000000000000000000000', { waitUntil: 'domcontentloaded' });
    if (EXPECTED === 'on') {
      await expect(page.getByText("This webhook doesn't exist or was deleted.")).toBeVisible({ timeout: UI_READY });
    } else {
      await expect(page).toHaveURL(/\/dashboard\/settings\/integrations$/, { timeout: UI_READY });
    }
  });
});
