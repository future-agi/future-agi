import { readFileSync } from 'node:fs';
import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { WebhookReceiver, signatureCount, verifiesWith } from '../../lib/webhook-receiver';

const UI_READY = 60_000;
// Pinned from futureagi/webhooks/urls.py (WH-2) and integrations/urls.py (WH-3).
const ENDPOINTS = '/webhooks/endpoints/';
const POLICY = '/webhooks/policy/';
const DESTINATIONS = '/integrations/destinations/';
// Copy pinned from frontend/src/sections/settings/integrations/webhooks/copy.js (DESIGN.md section 11.2).
const EMPTY_STATE = 'No webhooks yet. Send prompt and deployment events to your own HTTPS endpoint, signed so you can check they came from Future AGI.';
const SECRET_ONCE = "Copy this signing secret now. You won't see it again; if you lose it, rotate it.";
// Pinned from SigningSecretDialog.jsx's COPY_FAILED.
const COPY_FAILED_START = 'The secret was not copied: this browser blocked the clipboard.';

interface Envelope<T> { status: boolean; result: T }
interface SecretRow { id: string; tail: string; state: string }
interface EndpointRow {
  id: string; name: string; status: string; disabled_reason: string | null; url_host: string;
  workspace_id: string | null; org_wide: boolean; secrets?: SecretRow[] | null;
  subscriptions: { event_type: string }[];
}
interface DeliveryRow { id: string; is_test: boolean; status: string; attempts_count: number; event_type: string }
interface DeliveryDetail extends DeliveryRow { attempts: { n: number; response_status: number | null; trigger: string }[] }

test('SET-E2E-010: an admin adds a webhook, tests, redelivers, rotates its secret, turns it off and the policy blocks a host', {
  tag: ['@flow'],
  // The catalog text avoids '*': the pre-commit Prettier escapes it in FLOWS.md, which then no longer matches yarn catalog.
  annotation: flowAnnotation({
    id: 'SET-E2E-010', area: 'settings',
    userGoal: 'An admin sends Future AGI events to their own endpoint and manages it from Settings > Integrations > Webhooks',
    steps: [
      'open Settings > Integrations and see the Webhooks section empty state',
      'add a webhook for "A new version is committed" and save it',
      'copy the signing secret shown once, download it as an .env line, and send a test from the secret dialog',
      'open the endpoint from the list and the test delivery from its log',
      'redeliver the delivery from the drawer and see attempt 2 there',
      'rotate the secret from the More actions menu with a 24 hour overlap, send a test, then stop the previous secret',
      'turn the webhook off',
      'read what was done on the Activity card',
      'set the org policy to allow only acme.test subdomains and try to add the same URL and to turn the webhook on',
    ],
    backendChecks: [
      'GET /integrations/destinations/ lists the endpoint as a webhook destination',
      'GET /webhooks/endpoints/{id}/ shows only the secret tail, never the secret',
      'the receiver gets the test signed with the shown secret, with test: true and X-FutureAGI-Test: 1',
      'GET /webhooks/endpoints/{id}/deliveries/ has exactly the test delivery, succeeded',
      'the redelivery reaches the receiver with the same webhook-id as attempt 2, and the delivery has 2 attempts',
      'during the overlap the receiver gets two v1 signatures that verify with the old and the new secret; after stop, one active secret',
      'GET /webhooks/endpoints/{id}/ is disabled_manual after Turn off and stays so when Turn on is refused',
      'GET /webhooks/endpoints/{id}/activity/ lists the turn off first, then the stopped and rotated secrets, the redelivery, the test and the creation',
      'GET /webhooks/policy/ allows only the acme.test wildcard, and no second endpoint is created',
    ],
  }),
}, async ({ page, actor, browserName }, testInfo) => {
  // Ten UI stages, each bounded by UI_READY, plus three worker round trips.
  test.setTimeout(420_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-webhook-${suffix}`;
  const receiver = await WebhookReceiver.start(testInfo.parallelIndex, `/hook-${suffix}`);
  await testInfo.attach('seed', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, name, url: receiver.url }) });

  try {
    const before = await actor.api.get<Envelope<{ results: EndpointRow[] }>>(ENDPOINTS);
    expect(before.result.results.filter((e) => e.name === name)).toEqual([]);

    await test.step('UI: the Webhooks section is on the Integrations page', async () => {
      await page.goto('/dashboard/settings/integrations', { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: 'Webhooks', exact: true })).toBeVisible({ timeout: UI_READY });
      // The worker's org is shared with the other webhooks flow: the empty state is
      // asserted when this org has no webhook yet, the table otherwise.
      if (before.result.results.length === 0) {
        await expect(page.getByText(EMPTY_STATE)).toBeVisible({ timeout: UI_READY });
      } else {
        await expect(page.getByRole('table', { name: 'Webhooks' })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByText(EMPTY_STATE)).toHaveCount(0);
      }
    });

    let secret = '';
    await test.step('UI: add a webhook and see the request it will receive', async () => {
      await page.getByRole('button', { name: 'Add webhook' }).click();
      const dialog = page.getByRole('dialog', { name: 'Add webhook' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('textbox', { name: 'Name' }).fill(name);
      await dialog.getByRole('textbox', { name: 'Endpoint URL' }).fill(receiver.url);
      await dialog.getByRole('checkbox', { name: 'A new version is committed' }).check();
      const pane = dialog.getByLabel('What your endpoint receives');
      await expect(pane).toContainText('webhook-signature: v1,<signature>', { timeout: UI_READY });
      await expect(pane).toContainText('"type": "prompt.version.created"');
      await dialog.getByRole('button', { name: 'Save' }).click();

      const shown = page.getByRole('dialog', { name: `${name} is ready` });
      await expect(shown.getByText(SECRET_ONCE)).toBeVisible({ timeout: UI_READY });
      secret = await shown.getByRole('textbox', { name: 'Signing secret' }).inputValue();
      expect(secret).toMatch(/^whsec_[A-Za-z0-9+/=]{40,}$/);
    });

    const list = await actor.api.get<Envelope<{ results: EndpointRow[] }>>(ENDPOINTS);
    const created = list.result.results.filter((e) => e.name === name);
    expect(created).toHaveLength(1);
    const id = created[0].id;
    await testInfo.attach('endpoint', { contentType: 'application/json', body: JSON.stringify({ id }) });

    await test.step('API: the secret is shown once; only its tail is kept in view', async () => {
      const detail = await actor.api.get<Envelope<EndpointRow>>(`${ENDPOINTS}${id}/`);
      expect(detail.result.secrets?.map((s) => [s.state, s.tail])).toEqual([['active', secret.slice(-4)]]);
      expect(JSON.stringify(detail)).not.toContain(secret);
      expect(detail.result.subscriptions.map((s) => s.event_type)).toEqual(['prompt.version.created']);
      const reg = await actor.api.get<Envelope<{ destinations: { kind: string; id: string; name: string }[] }>>(DESTINATIONS);
      expect(reg.result.destinations.filter((d) => d.id === id)).toEqual([
        expect.objectContaining({ kind: 'webhook', id, name }),
      ]);
    });

    await test.step('UI: Copy puts the shown secret on the clipboard where the browser lets the test read it', async () => {
      // WH-3 review S9. Chromium grants the test clipboard access; elsewhere the dialog must still say what happened.
      const shown = page.getByRole('dialog', { name: `${name} is ready` });
      if (browserName === 'chromium') await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
      await shown.getByRole('button', { name: 'Copy', exact: true }).click();
      const copied = shown.getByRole('button', { name: 'Copied', exact: true });
      const refused = shown.getByText(COPY_FAILED_START);
      await expect(copied.or(refused)).toBeVisible({ timeout: UI_READY });
      if (browserName === 'chromium') {
        await expect(copied).toBeVisible();
        expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(secret);
      }
    });

    await test.step('UI: Download as .env line saves the shown secret', async () => {
      // WH-3 review S9: the object URL outlives the click, so every browser completes the download.
      const shown = page.getByRole('dialog', { name: `${name} is ready` });
      const download = page.waitForEvent('download', { timeout: UI_READY });
      await shown.getByRole('button', { name: 'Download as .env line' }).click();
      const file = await download;
      expect(file.suggestedFilename()).toBe('futureagi-webhook.env');
      const text = readFileSync(await file.path(), 'utf8');
      expect(text.trim()).toMatch(/^[A-Z_]+=whsec_/);
      expect(text).toContain(secret);
    });

    await test.step('UI: send a test from the secret dialog', async () => {
      const shown = page.getByRole('dialog', { name: `${name} is ready` });
      await shown.getByRole('button', { name: 'Send a test now' }).click();
      await expect(shown.getByRole('status')).toHaveText(/^Your endpoint answered 200 in [\d,]+ ms\./, { timeout: UI_READY });
    });

    await test.step('Receiver: the test is signed with the shown secret', async () => {
      expect(receiver.requests).toHaveLength(1);
      const req = receiver.requests[0];
      expect(verifiesWith(secret, req)).toBe(true);
      expect(verifiesWith(`whsec_${Buffer.alloc(32, 1).toString('base64')}`, req)).toBe(false);
      expect(req.headers['x-futureagi-test']).toBe('1');
      expect(receiver.json(0)).toMatchObject({ type: 'prompt.version.created', test: true });
    });

    await test.step('UI: the endpoint is listed as healthy and opens', async () => {
      await page.getByRole('dialog', { name: `${name} is ready` }).getByRole('button', { name: 'Done' }).click();
      const row = page.getByRole('row').filter({ hasText: name });
      await expect(row).toHaveCount(1, { timeout: UI_READY });
      await expect(row.getByRole('cell').nth(1)).toContainText(new URL(receiver.url).hostname);
      await expect(row.getByText('Healthy', { exact: true })).toBeVisible();
      await row.click();
      await expect(page).toHaveURL(new RegExp(`/dashboard/settings/integrations/webhooks/${id}$`), { timeout: UI_READY });
      await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible({ timeout: UI_READY });
    });

    let deliveryId = '';
    await test.step('UI + API: the test delivery is in the log', async () => {
      const log = page.getByRole('table', { name: 'Delivery log' });
      const rows = log.getByRole('row').filter({ hasText: 'prompt.version.created' });
      await expect(rows).toHaveCount(1, { timeout: UI_READY });
      await expect(rows.first()).toContainText('Test');
      await expect(rows.first()).toContainText('Delivered');
      const d = await actor.api.get<Envelope<{ results: DeliveryRow[] }>>(`${ENDPOINTS}${id}/deliveries/`);
      expect(d.result.results.map((r) => [r.is_test, r.status, r.attempts_count])).toEqual([[true, 'succeeded', 1]]);
      deliveryId = d.result.results[0].id;
      await rows.first().click();
    });

    await test.step('UI: redeliver from the drawer', async () => {
      const drawer = page.getByRole('dialog', { name: 'Delivery' });
      await expect(drawer.getByLabel('Attempts')).toContainText(`Attempt 1`, { timeout: UI_READY });
      await expect(drawer.getByLabel('Attempts')).toContainText(`200 from ${new URL(receiver.url).hostname}`);
      await drawer.getByRole('button', { name: 'Redeliver' }).click();
      await expect(drawer.getByRole('status')).toHaveText('Redelivery queued as attempt 2.', { timeout: UI_READY });
    });

    await test.step('Receiver + API: the same event arrives again as attempt 2', async () => {
      await expect.poll(() => receiver.requests.length, POLL.ASYNC_JOB).toBe(2);
      const [first, again] = receiver.requests;
      expect(again.headers['webhook-id']).toBe(first.headers['webhook-id']);
      expect(again.headers['x-futureagi-delivery']).toBe(deliveryId);
      expect(again.headers['x-futureagi-attempt']).toBe('2');
      expect(verifiesWith(secret, again)).toBe(true);
      await expect.poll(async () => {
        const d = await actor.api.get<Envelope<DeliveryDetail>>(`/webhooks/deliveries/${deliveryId}/`);
        return d.result.attempts.map((a) => [a.n, a.response_status]);
      }, POLL.ASYNC_JOB).toEqual([[1, 200], [2, 200]]);
      // Review S1: the open drawer shows attempt 2 by itself, without being reopened.
      const drawer = page.getByRole('dialog', { name: 'Delivery' });
      await expect(drawer.getByLabel('Attempts')).toContainText('Attempt 2', { timeout: UI_READY });
      await drawer.getByRole('button', { name: 'Close' }).click();
    });

    let rotated = '';
    await test.step('UI: rotate the secret with a 24 hour overlap', async () => {
      // Review S6: the ⋯ menu's Rotate secret opens the rotate dialog itself.
      await page.getByRole('button', { name: 'More actions' }).click();
      await page.getByRole('menuitem', { name: 'Rotate secret' }).click();
      const dialog = page.getByRole('dialog', { name: 'Rotate the signing secret' });
      await expect(dialog.getByText('Both secrets sign every request until then.')).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Rotate', exact: true }).click();
      const shown = page.getByRole('dialog', { name: 'New signing secret' });
      rotated = await shown.getByRole('textbox', { name: 'Signing secret' }).inputValue();
      expect(rotated).toMatch(/^whsec_/);
      expect(rotated).not.toBe(secret);
      await shown.getByRole('button', { name: 'Done' }).click();
      const card = page.getByRole('region', { name: 'Signing secret' });
      await expect(card.getByText(`Active: …${rotated.slice(-4)}`, { exact: false })).toBeVisible({ timeout: UI_READY });
      await expect(card.getByText(`Previous: …${secret.slice(-4)}`, { exact: false })).toBeVisible();
    });

    await test.step('UI + receiver: during the overlap both secrets sign', async () => {
      await page.getByRole('button', { name: 'Send test' }).click();
      await page.getByRole('menuitem', { name: 'prompt.version.created' }).click();
      await expect(page.getByRole('status').filter({ hasText: 'Your endpoint answered' }))
        .toHaveText(/^Your endpoint answered 200/, { timeout: UI_READY });
      expect(receiver.requests).toHaveLength(3);
      const req = receiver.requests[2];
      expect(signatureCount(req)).toBe(2);
      expect(verifiesWith(rotated, req)).toBe(true);
      expect(verifiesWith(secret, req)).toBe(true);
    });

    await test.step('UI + API: stop the previous secret now', async () => {
      const card = page.getByRole('region', { name: 'Signing secret' });
      await card.getByRole('button', { name: 'Stop it now' }).click();
      await expect(card.getByText(/^Previous:/)).toHaveCount(0, { timeout: UI_READY });
      const detail = await actor.api.get<Envelope<EndpointRow>>(`${ENDPOINTS}${id}/`);
      expect(detail.result.secrets?.map((s) => [s.state, s.tail])).toEqual([['active', rotated.slice(-4)]]);
    });

    await test.step('UI + API: turn the webhook off', async () => {
      await page.getByRole('button', { name: 'Turn off' }).click();
      await expect(page.getByRole('group', { name: 'Webhook status' })).toHaveText('Turned off', { timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Turn on' })).toBeVisible();
      const detail = await actor.api.get<Envelope<EndpointRow>>(`${ENDPOINTS}${id}/`);
      expect([detail.result.status, detail.result.disabled_reason]).toEqual(['disabled_manual', 'manual']);
    });

    await test.step('UI + API: the Activity card lists what was done, newest first', async () => {
      const card = page.getByRole('region', { name: 'Activity' });
      const rows = card.getByRole('table', { name: 'Activity entries' }).getByRole('row');
      await expect(rows.nth(1)).toContainText('Turned off', { timeout: UI_READY });
      for (const what of [/^Previous signing secret stopped/, /^Signing secret rotated/, /^Redelivered: attempt 2/,
        /^Test sent/, /^Created/]) {
        await expect(card.getByRole('cell', { name: what }).first()).toBeVisible();
      }
      const activity = await actor.api.get<Envelope<{ results: { action: string; actor: { name: string } | null }[] }>>(
        `${ENDPOINTS}${id}/activity/`);
      const actions = activity.result.results.map((e) => e.action);
      expect(actions[0]).toBe('webhook.endpoint.disabled');
      expect(actions).toEqual(expect.arrayContaining(['webhook.secret.previous_revoked', 'webhook.secret.rotated',
        'webhook.delivery.redelivered', 'webhook.test.sent', 'webhook.endpoint.created']));
      expect(actions.indexOf('webhook.secret.rotated')).toBeLessThan(actions.indexOf('webhook.endpoint.created'));
      expect(activity.result.results[0].actor?.name).toBeTruthy();
    });

    await test.step('UI: the org policy allows only *.acme.test', async () => {
      await page.goto('/dashboard/settings/integrations', { waitUntil: 'domcontentloaded' });
      const card = page.getByRole('region', { name: 'Webhook policy' });
      const hosts = card.getByRole('combobox', { name: 'Allowed hosts' });
      await expect(hosts).toBeVisible({ timeout: UI_READY });
      await hosts.fill('*.acme.test');
      await hosts.press('Enter');
      await card.getByRole('button', { name: 'Save policy' }).click();
      await expect(card.getByRole('status')).toContainText('Policy saved.', { timeout: UI_READY });
      const policy = await actor.api.get<Envelope<{ allowed_hosts: string[] }>>(POLICY);
      expect(policy.result.allowed_hosts).toEqual(['*.acme.test']);
    });

    await test.step('UI: the policy refuses the host on add and on turn on', async () => {
      await page.getByRole('button', { name: 'Add webhook' }).click();
      const dialog = page.getByRole('dialog', { name: 'Add webhook' });
      await dialog.getByRole('textbox', { name: 'Name' }).fill(`${name}-again`);
      await dialog.getByRole('textbox', { name: 'Endpoint URL' }).fill(receiver.url);
      await dialog.getByRole('checkbox', { name: 'A new version is committed' }).check();
      await dialog.getByRole('button', { name: 'Save' }).click();
      await expect(dialog.getByText('only allows webhooks to *.acme.test')).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      const after = await actor.api.get<Envelope<{ results: EndpointRow[] }>>(ENDPOINTS);
      expect(after.result.results.filter((e) => e.name.startsWith(name)).map((e) => e.id)).toEqual([id]);

      await page.goto(`/dashboard/settings/integrations/webhooks/${id}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Turn on' }).click({ timeout: UI_READY });
      await expect(page.getByRole('alert').filter({ hasText: 'only allows webhooks to *.acme.test' }))
        .toBeVisible({ timeout: UI_READY });
      const detail = await actor.api.get<Envelope<EndpointRow>>(`${ENDPOINTS}${id}/`);
      expect(detail.result.status).toBe('disabled_manual');
      expect(receiver.requests).toHaveLength(3);
    });
  } finally {
    // The actor's org is shared by the worker's other flows: give the policy back.
    await actor.api.put(POLICY, { allowed_hosts: [] }).catch((e: unknown) => {
      if (!(e instanceof ApiError)) throw e;
    });
    await receiver.stop();
  }
});
