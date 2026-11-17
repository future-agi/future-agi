import { request } from '@playwright/test';
import pg from 'pg';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/routes/sections/dashboard.jsx: gateway "webhooks".
const WEBHOOKS_ROUTE = '/dashboard/gateway/webhooks';
// agentcc/urls.py: AgentccWebhookViewSet and AgentccAPIKeyViewSet.
const WEBHOOKS = '/agentcc/webhooks/';
const API_KEYS = '/agentcc/api-keys/';
// GatewayWebhookView, and the secret docker-compose.e2e.yml gives the backend.
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET = process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
// docker-compose.e2e.yml sets AGENTCC_WEBHOOK_DISABLE_AFTER_FAILURES=3 and
// AGENTCC_WEBHOOK_DISABLE_AFTER_MINUTES=0 on the worker, which runs the
// delivery schedule.
const DISABLE_AFTER = 3;
// Five events, so each of the three failing runs has a due one: an attempt
// the delivery guard refuses gets no HTTP answer, so its run stops there and
// that event backs off 30 s.
const EVENTS = 5;
// tfc/temporal/schedules/agentcc_webhooks.py: agentcc-webhook-delivery fires
// every 10 s. After each failing run the webhook's next run waits 10 s, then
// 20 s (agentcc/services/webhook_delivery.py), so three failures take about a
// minute plus worker start-up. ASYNC_JOB is one Temporal job; this is a chain of them.
const DISABLED_WITHIN = { timeout: 3 * POLL.ASYNC_JOB.timeout, intervals: [2_000] };
const UI_READY = 60_000;

type Created = { result: { id: string } };
type Key = { result: { gateway_key_id: string } };

test('GW-E2E-006: a webhook that keeps failing is disabled with its reason, re-enabled, and deleted without leftover events', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-006',
    area: 'gateway',
    userGoal: 'A platform engineer sees a webhook failing, then why the gateway disabled it, turns it back on with its events still waiting, and deletes it without leaving events to deliver',
    steps: [
      'create a gateway key and an outbound webhook for request.completed',
      'point the webhook at an in-stack host the delivery guard refuses, so every attempt fails on the VM',
      'deliver five gateway request logs on the logs webhook',
      'open Gateway Webhooks while it is still active and read its failed deliveries in a row',
      'wait for the delivery schedule to disable the webhook after three failed deliveries',
      'open Gateway Webhooks and read the Disabled status and its reason',
      'choose Enable from the row actions and see it Active',
      'delete the webhook from the row actions',
    ],
    backendChecks: [
      'log ingest answers at once and leaves its five events pending, none delivered',
      'after its first failed delivery the webhook in PG agentcc_webhook is still active with a failure streak',
      'the webhook in PG agentcc_webhook is inactive with a reason naming three consecutive failed deliveries, and its five events are still pending',
      'enabling it stores is_active true with the failure streak and reason cleared',
      'after the delete none of its events is pending, failed or dead-lettered; all five are cancelled',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seeding, up to three Temporal delivery ticks, three UI stages.
  test.setTimeout(DISABLED_WITHIN.timeout + 4 * UI_READY + 60_000);
  const name = `e2e-wh-${testInfo.workerIndex}-${Date.now().toString(36)}`;

  const key = await actor.api.post<Key>(API_KEYS, { name });
  // A public placeholder, as flows/alerts/webhook-configuration.spec.ts uses:
  // the serializer refuses private hosts at save time.
  const webhook = await actor.api.post<Created>(WEBHOOKS, {
    name,
    url: `http://93.184.216.34/${name}`,
    events: ['request.completed'],
  });
  const webhookId = webhook.result.id;
  await testInfo.attach('seeded-ids', {
    body: JSON.stringify({ organization: actor.organizationId, webhook: webhookId, gatewayKeyId: key.result.gateway_key_id, name }),
    contentType: 'application/json',
  });

  // Before any event exists, point the webhook at a compose-network host. The
  // delivery guard (agentcc/services/url_safety.py) refuses it before any
  // connection, so every attempt fails fast and nothing leaves the VM. The
  // probe stays read-only; this one seeding write has its own connection.
  const seedClient = new pg.Client({ connectionString: E2E.pgUrl });
  await seedClient.connect();
  try {
    const repointed = await seedClient.query(
      'UPDATE agentcc_webhook SET url = $2 WHERE id = $1 AND organization_id = $3',
      [webhookId, `http://mock-llm:8080/${name}`, actor.organizationId],
    );
    expect(repointed.rowCount).toBe(1);
  } finally {
    await seedClient.end();
  }

  const events = () => probe.pg<{ status: string; attempts: number }>(
    'SELECT status, attempts FROM agentcc_webhook_event WHERE webhook_id = $1 ORDER BY created_at',
    [webhookId],
  );
  const stored = async () => (await probe.pg<{ is_active: boolean; consecutive_failures: number; disabled_reason: string }>(
    'SELECT is_active, consecutive_failures, disabled_reason FROM agentcc_webhook WHERE id = $1',
    [webhookId],
  ))[0];

  await test.step('ingest only records the events', async () => {
    const req = await request.newContext();
    const started = Date.now();
    const res = await req.post(`${E2E.apiUrl}${LOGS_WEBHOOK_PATH}`, {
      headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
      data: {
        logs: Array.from({ length: EVENTS }, (_, i) => ({
          request_id: `${name}-${i}`,
          auth_key_id: key.result.gateway_key_id,
          model: 'gpt-4o-mini',
          provider: 'openai',
          status_code: 200,
          latency_ms: 120,
          timestamp: new Date().toISOString(),
        })),
      },
    });
    // Ingest delivers nothing itself, so it does not wait on the webhook.
    // Generous for a loaded runner; inline delivery of five events to a
    // refused host would still finish fast, so the storage reads below are
    // what prove no attempt was made here.
    const ingestMs = Date.now() - started;
    expect(res.status(), await res.text()).toBe(200);
    expect(await res.json()).toMatchObject({ result: { ingested: EVENTS } });
    expect(ingestMs).toBeLessThan(5_000);
    await req.dispose();
    // Read straight after the 200: a tick may start before this read, so only
    // the events still untried are counted, and none can have been delivered.
    const rows = await events();
    expect(rows).toHaveLength(EVENTS);
    expect(rows.filter(r => r.status === 'delivered')).toEqual([]);
  });

  const row = page.getByRole('row').filter({ hasText: name });
  // Holds the webhook's next run back (its failure pause) while the page is
  // read, so a slow runner cannot reach the third failure mid-check; then lets
  // delivery go on. A seeding write, on its own connection like the repoint.
  const holdDelivery = async (hold: boolean) => {
    const client = new pg.Client({ connectionString: E2E.pgUrl });
    await client.connect();
    try {
      const held = await client.query(
        `UPDATE agentcc_webhook SET next_delivery_at = ${hold ? "now() + interval '10 minutes'" : 'NULL'}
          WHERE id = $1 AND organization_id = $2`,
        [webhookId, actor.organizationId],
      );
      expect(held.rowCount).toBe(1);
    } finally {
      await client.end();
    }
  };
  await test.step('while it is still active the dashboard shows it failing', async () => {
    // The first failed run pauses the webhook 10 s, so it is active with a
    // streak of one when the poll sees the failure.
    await expect.poll(async () => (await stored()).consecutive_failures, DISABLED_WITHIN).toBeGreaterThan(0);
    await holdDelivery(true);
    const failing = await stored();
    expect(failing.is_active).toBe(true);
    await page.goto(WEBHOOKS_ROUTE);
    await expect(row).toHaveCount(1, { timeout: UI_READY });
    await expect(row.getByText('Active', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(row.getByText(`${failing.consecutive_failures} failed ${failing.consecutive_failures === 1 ? 'delivery' : 'deliveries'} in a row`, { exact: true })).toBeVisible({ timeout: UI_READY });
    await holdDelivery(false);
  });

  await test.step('the delivery schedule disables the webhook after three failures', async () => {
    await expect.poll(async () => (await stored()).is_active, DISABLED_WITHIN).toBe(false);
    const disabled = await stored();
    expect(disabled.consecutive_failures).toBe(DISABLE_AFTER);
    expect(disabled.disabled_reason).toContain(`Disabled after ${DISABLE_AFTER} consecutive failed deliveries`);
    const rows = await events();
    // Disabling keeps them pending, for when it is enabled again.
    expect(rows.map(r => r.status)).toEqual(Array(EVENTS).fill('pending'));
    // One failed attempt per run, each one counted: three in all, so the
    // webhook's other events waited untouched.
    expect(rows.reduce((sum, r) => sum + r.attempts, 0)).toBe(DISABLE_AFTER);
  });

  await test.step('the dashboard shows Disabled and why', async () => {
    await page.reload();
    await expect(row).toHaveCount(1, { timeout: UI_READY });
    await expect(row.getByText('Disabled', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(row.getByText(/Disabled after 3 consecutive failed deliveries/)).toBeVisible({ timeout: UI_READY });
  });

  await test.step('Enable turns it back on and clears the streak', async () => {
    const saved = page.waitForResponse(
      res => res.url().endsWith(`${WEBHOOKS}${webhookId}/`) && res.request().method() === 'PATCH',
      { timeout: UI_READY },
    );
    await row.getByRole('button', { name: 'Webhook actions' }).click();
    await page.getByRole('menuitem', { name: 'Enable', exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    expect(await response.json()).toMatchObject({
      result: { is_active: true, consecutive_failures: 0, disabled_reason: '' },
    });
    await expect(page.getByText('Webhook enabled')).toBeVisible({ timeout: UI_READY });
    await expect(row.getByText('Active', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(row.getByText(/consecutive failed deliveries/)).toHaveCount(0);
    // Its events wait for the next run again; a run may already have made a
    // new attempt, so only the stored state the PATCH set is read here.
    expect(await stored()).toMatchObject({ is_active: true, disabled_reason: '' });
    expect((await events()).map(r => r.status)).toEqual(Array(EVENTS).fill('pending'));
  });

  await test.step('deleting it leaves no event to deliver', async () => {
    const deleted = page.waitForResponse(
      res => res.url().endsWith(`${WEBHOOKS}${webhookId}/`) && res.request().method() === 'DELETE',
      { timeout: UI_READY },
    );
    await row.getByRole('button', { name: 'Webhook actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
    expect((await deleted).status()).toBe(200);
    await expect(row).toHaveCount(0, { timeout: UI_READY });
    const rows = await events();
    expect(rows.filter(r => ['pending', 'failed', 'dead_letter'].includes(r.status))).toEqual([]);
    expect(rows.map(r => r.status)).toEqual(Array(EVENTS).fill('cancelled'));
  });
});
