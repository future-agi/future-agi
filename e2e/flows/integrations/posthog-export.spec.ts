import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import type { Page, Response } from '@playwright/test';
import { inspectManagedMock } from '../../lib/managed-mock';
import { mintScope, WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { posthogHost, refusedCredential } from '../../lib/export-sinks';
import {
  CONNECTIONS_PATH, connectExport, deleteConnection, expectConnectionShown, expectDeletedConnectionPage,
  expectedRequestIds, expectSyncApi, expectSyncHistory, firstSync, mintRequestLogs, openPlatform, shot,
  type SyncRow, validateCredentials,
} from '../../lib/integration-export';

test.skip(process.env.E2E_STACK === 'standalone', 'The export sinks are wired on the Distributed stack only');

// Pinned from frontend/src/sections/settings/integrations/AddIntegrationWizard/
// credentials/PosthogCredentials.jsx (the self-hosted toggle and its fields)
// and integrations/services/posthog_service.py: the connection check posts
// <host>/decide/?v=3, the export posts <host>/batch/ with
// { api_key, batch: [{ event, properties, timestamp, distinct_id }] }
// (PostHog's documented batch API), one event per request log
// (integrations/schemas.py ExportEvent), up to 500 per batch
// (activities.py _batch_export). An edited host is checked at its
// /decide/ before it is stored (views/integration_connection.py
// _update_connection), and the next sync sends there. Pause, Resume and Sync
// now are the connection page's (IntegrationDetail.jsx); Sync now is refused
// within 60 s of the last sync (views/integration_connection.py sync_now), so
// the flow waits that out before it asks for the second sync. A project API
// key PostHog refuses answers 401 at /decide/ (posthog_service.py
// validate_credentials: the wizard's refusal); the sink refuses a minted
// e2e-refused-* key (stack/webhook-sink/server.mjs). A deleted connection's
// detail read answers 404 "Integration connection not found."
// (views/integration_connection.py retrieve), which its page shows inline
// (IntegrationDetail.jsx) and no toast repeats (the read is errorHandled,
// frontend/src/api/integrations).
const BATCH_SIZE = 500;
const SYNC_NOW_COOLDOWN_S = 60;
const UI_READY = 30_000;
const REFUSED_KEY = 'Invalid API key. Please check your PostHog project API key.';
// Each step's budget; the test's ceiling is their sum.
const STEP = {
  credentials: 2 * UI_READY + 30_000, create: 60_000, sync: POLL.ASYNC_JOB.timeout, sink: 30_000, history: 60_000,
  shown: 60_000, edit: 60_000, pause: 60_000, cooldown: (SYNC_NOW_COOLDOWN_S + 15) * 1000,
  syncNow: 30_000 + POLL.ASYNC_JOB.timeout, remove: 60_000, deleted: 60_000,
};

const isCall = (r: Response, method: string, path: string) =>
  r.request().method() === method && new URL(r.url()).pathname === path;

/** Clicks the connection page button `name` and returns its call's status. */
async function press(page: Page, name: string, path: string, method = 'POST'): Promise<number> {
  const call = page.waitForResponse((r) => isCall(r, method, path), { timeout: UI_READY });
  await page.getByRole('button', { name, exact: true }).click();
  return (await call).status();
}

interface PosthogEvent {
  event: string; distinct_id: string; timestamp: string; properties: Record<string, unknown>;
}

test(
  'INT-E2E-001: gateway request logs export to a self-hosted PostHog',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'INT-E2E-001',
      area: 'integrations',
      userGoal:
        'An admin connects a self-hosted PostHog and finds the organization\'s gateway requests there as events',
      steps: [
        'deliver three gateway request logs, one of them a 500, on the gateway logs webhook',
        'open Settings > Integrations and pick PostHog',
        'switch to self-hosted PostHog, enter its host URL and a project API key PostHog refuses, and Validate & Continue: read the refusal',
        'enter the project API key, and Validate & Continue',
        'keep "Export the last 24 hours" and Connect Integration',
        'open the connection and read its Sync History',
        'go Back to Integrations and read the connection\'s card under Connections, then its page header',
        'open Edit and Cancel it; Edit it again: rename it, enter a second PostHog host and pick "Every 15 minutes", and Save Changes',
        'Pause it, then Resume it',
        'deliver one more request log, wait out the 60 s Sync now cooldown and press Sync Now',
        'delete the connection from its page, cancelling once first',
        'open the deleted connection\'s page by its address, read why it cannot be shown, and go Back to Integrations',
      ],
      backendChecks: [
        'the managed stack is the attested mock stack with the export sinks declared',
        'the refused key reached the PostHog host\'s /decide/ once and was answered 401, and no create was sent',
        'the connection check reached the PostHog host twice more (the validate step and the create), at /decide/',
        'the first sync finished success in PG integrations_sync_log with the count of request logs it sent',
        'the events PostHog received at /batch/ are exactly the org\'s request logs in the sync window, the three minted ones among them',
        'each batch carries the project API key, and each minted request arrived as an agentcc_request event with its model, provider, status, tokens and time',
        'the sync-logs API and the connection detail agree: one success row with that count, the connection active',
        'the connection is stored with the PostHog address entered and no FutureAGI project, and its card and page header show that address, its name and its exported count',
        'the second host was checked at its /decide/ before the edit was stored (display name, host, 900 s interval), and the page shows all three',
        'Pause and Resume each change the stored status, and the page badge follows',
        'Sync Now runs a second sync that sends exactly the request logs of its window, the new one among them, to the second host and nothing more to the first, and the page lists both syncs',
        'deleting it soft-deletes it in PG and drops it from the connections API',
        'the deleted connection\'s detail read answers 404 and its page says so once, inline, with no toast',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(Object.values(STEP).reduce((a, b) => a + b, 0));
    inspectManagedMock({ exportSinks: true });
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const minted = await mintRequestLogs(actor, probe, `e2e-int-ph-${suffix}`);
    const channel = mintScope('posthog');
    const movedTo = mintScope('posthog-moved');
    const apiKey = `phc_e2e_${suffix}`;
    const refusedKey = refusedCredential(`ph-${suffix}`);
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: actor.organizationId, channel,
        requestIds: minted.map((m) => m.request_id) }),
    });

    await test.step('connect a self-hosted PostHog', async () => {
      await openPlatform(page, 'PostHog', UI_READY);
      await page.getByRole('button', { name: 'Using self-hosted PostHog?' }).click();
      await page.getByLabel('Host URL').fill(posthogHost(channel));
      let creates = 0;
      const count = (r: { method(): string; url(): string }) => {
        if (r.method() === 'POST' && new URL(r.url()).pathname === CONNECTIONS_PATH) creates += 1;
      };
      page.on('request', count);
      await page.getByLabel('Project API Key').fill(refusedKey);
      expect(await validateCredentials(page, UI_READY)).toBe(400);
      await expect(page.getByRole('alert').filter({ hasText: REFUSED_KEY })).toHaveText([REFUSED_KEY],
        { timeout: UI_READY });
      page.off('request', count);
      expect(creates).toBe(0);
      await shot(page, testInfo, 'credentials-refused');
      await page.getByLabel('Project API Key').fill(apiKey);
      await shot(page, testInfo, 'credentials');
      expect(await validateCredentials(page, UI_READY)).toBe(200);
    }, { timeout: STEP.credentials });
    const connectionId = await test.step('connect the export', async () => {
      await shot(page, testInfo, 'sync-settings');
      const id = await connectExport(page, UI_READY,
        { interval: 'Every 30 minutes', sends: { host_url: posthogHost(channel), display_name: 'PostHog' } });
      await shot(page, testInfo, 'connected');
      return id;
    }, { timeout: STEP.create });
    await testInfo.attach('connection', { contentType: 'text/plain', body: connectionId });

    const sync = await firstSync(probe, connectionId);
    expect([sync.status, sync.error_message]).toEqual(['success', '']);
    const expected = await expectedRequestIds(probe, actor, sync);
    expect(expected).toEqual(expect.arrayContaining(minted.map((m) => m.request_id)));
    expect(sync.traces_fetched).toBe(expected.length);

    expect(await probe.pg<{ host_url: string; project_id: string | null }>(
      'SELECT host_url, project_id FROM integrations_connection WHERE id = $1', [connectionId]))
      .toEqual([{ host_url: posthogHost(channel), project_id: null }]);

    await test.step('PostHog received exactly the window\'s request logs', async () => {
      const received = await new WebhookSink().received(channel);
      const route = (path: string) => new URL(path, 'http://sink').pathname.replace(`/r/${channel}/as/posthog`, '');
      const batches = received.filter((r) => route(r.path) === '/batch/');
      expect(received.map((r) => `${r.method} ${route(r.path)} ${r.status}`)).toEqual([
        'POST /decide/ 401', 'POST /decide/ 200', 'POST /decide/ 200',
        ...Array(Math.ceil(expected.length / BATCH_SIZE)).fill('POST /batch/ 200'),
      ]);
      expect(received.slice(0, 3).map((r) => (r.body as { api_key: string }).api_key))
        .toEqual([refusedKey, apiKey, apiKey]);
      const bodies = batches.map((r) => r.body as { api_key: string; batch: PosthogEvent[] });
      expect(bodies.map((b) => b.api_key)).toEqual(bodies.map(() => apiKey));
      const events = bodies.flatMap((b) => b.batch);
      expect(events.map((e) => e.properties.request_id).sort()).toEqual(expected);
      for (const log of minted) {
        const event = events.find((e) => e.properties.request_id === log.request_id)!;
        expect(event).toMatchObject({
          event: 'agentcc_request', distinct_id: 'agentcc-gateway',
          properties: {
            token: apiKey, model: log.model, provider: log.provider, status_code: log.status_code,
            is_error: log.status_code >= 400, total_tokens: log.total_tokens, latency_ms: log.latency_ms,
            input_tokens: log.input_tokens, output_tokens: log.output_tokens, cost: Number(log.cost),
          },
        });
        expect(Date.parse(event.timestamp)).toBe(Date.parse(log.timestamp));
      }
    }, { timeout: STEP.sink });

    await test.step('the connection page shows the export', async () => {
      await expectSyncHistory(page, connectionId, expected.length, UI_READY);
      await shot(page, testInfo, 'sync-history');
    }, { timeout: STEP.history });
    await expectSyncApi(actor, connectionId, expected.length);
    const destination = posthogHost(channel);
    await test.step('the card and the page say where it delivers', () =>
      expectConnectionShown(page, testInfo, connectionId,
        { name: 'PostHog', destination, platform: 'PostHog', exported: expected.length, interval: '30 min' }, UI_READY),
    { timeout: STEP.shown });

    const renamed = `PostHog ${suffix}`;
    const moved = posthogHost(movedTo);
    const detailPath = `${CONNECTIONS_PATH}${connectionId}/`;
    const stored = async () => (await probe.pg<{
      display_name: string; host_url: string; sync_interval_seconds: number; status: string;
    }>('SELECT display_name, host_url, sync_interval_seconds, status FROM integrations_connection WHERE id = $1',
      [connectionId]))[0];
    const routes = async (at: string) => (await new WebhookSink().received(at))
      .map((r) => `${r.method} ${new URL(r.path, 'http://sink').pathname.replace(`/r/${at}/as/posthog`, '')} ${r.status}`);
    await test.step('edit its name, host and interval', async () => {
      const openEdit = async () => {
        await page.getByRole('button', { name: 'Integration options menu' }).click();
        await page.getByRole('menuitem', { name: 'Edit' }).click();
      };
      const dialog = page.getByRole('dialog', { name: 'Edit Integration' });
      await openEdit();
      await dialog.getByLabel('Display Name').fill(`${renamed} (cancelled)`);
      await dialog.getByLabel('Host URL').fill(moved);
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await expect(dialog).toBeHidden();
      expect(await stored()).toMatchObject({ display_name: 'PostHog', host_url: destination, sync_interval_seconds: 1800 });
      expect(await routes(movedTo)).toEqual([]);
      await openEdit();
      await expect(dialog.getByLabel('Display Name')).toHaveValue('PostHog');
      await expect(dialog.getByLabel('Host URL')).toHaveValue(destination);
      await expect(dialog.getByLabel('Public Key')).toHaveCount(0);
      await dialog.getByLabel('Display Name').fill(renamed);
      await dialog.getByLabel('Host URL').fill(moved);
      await dialog.getByLabel('Sync Interval').selectOption({ label: 'Every 15 minutes' });
      await shot(page, testInfo, 'edit-dialog');
      expect(await press(page, 'Save Changes', detailPath, 'PATCH')).toBe(200);
      await expect(dialog).toBeHidden();
      expect(await routes(movedTo)).toEqual(['POST /decide/ 200']);
      await expect(page.getByTestId('integration-header-destination')).toHaveText(moved);
      await expect(page.getByTestId('integration-header-destination').locator('xpath=preceding-sibling::*[1]'))
        .toHaveText(renamed);
      await expect(page.locator('.MuiGrid-item').filter({ has: page.getByText('Sync Interval', { exact: true }) })
        .getByText('15 min', { exact: true })).toBeVisible();
      expect(await stored()).toMatchObject({ display_name: renamed, host_url: moved, sync_interval_seconds: 900 });
      await shot(page, testInfo, 'edited');
    }, { timeout: STEP.edit });

    await test.step('pause and resume it', async () => {
      expect(await press(page, 'Pause', `${detailPath}pause/`)).toBe(200);
      await expect(page.getByText('Paused', { exact: true })).toBeVisible();
      expect((await stored()).status).toBe('paused');
      await shot(page, testInfo, 'paused');
      expect(await press(page, 'Resume', `${detailPath}resume/`)).toBe(200);
      await expect(page.getByText('Active', { exact: true })).toBeVisible();
      expect((await stored()).status).toBe('active');
    }, { timeout: STEP.pause });

    const [late] = await test.step('wait out the Sync now cooldown', async () => {
      await expect.poll(async () => Number((await probe.pg<{ age: string }>(
        'SELECT extract(epoch FROM now() - last_synced_at)::text AS age FROM integrations_connection WHERE id = $1',
        [connectionId]))[0].age), { timeout: STEP.cooldown, intervals: [5000] }).toBeGreaterThan(SYNC_NOW_COOLDOWN_S + 1);
      return mintRequestLogs(actor, probe, `e2e-int-ph2-${suffix}`, 1);
    }, { timeout: STEP.cooldown + 15_000 });

    await test.step('Sync Now exports what arrived since', async () => {
      expect(await press(page, 'Sync Now', `${detailPath}sync_now/`)).toBe(200);
      const second = async () => (await probe.pg<SyncRow>(
        `SELECT status, traces_fetched, spans_synced, sync_from::text AS sync_from, sync_to::text AS sync_to,
                COALESCE(error_message, '') AS error_message FROM integrations_sync_log
          WHERE connection_id = $1 AND completed_at IS NOT NULL ORDER BY started_at`, [connectionId]))[1];
      await expect.poll(async () => (await second())?.status ?? 'pending', POLL.ASYNC_JOB).not.toBe('pending');
      const sync2 = await second();
      expect([sync2.status, sync2.error_message]).toEqual(['success', '']);
      const expected2 = await expectedRequestIds(probe, actor, sync2);
      expect(expected2).toContain(late.request_id);
      expect(sync2.traces_fetched).toBe(expected2.length);
      expect((await routes(channel)).length).toBe(3 + Math.ceil(expected.length / BATCH_SIZE));
      expect(await routes(movedTo)).toEqual([
        'POST /decide/ 200', ...Array(Math.ceil(expected2.length / BATCH_SIZE)).fill('POST /batch/ 200'),
      ]);
      const batches = (await new WebhookSink().received(movedTo))
        .filter((r) => new URL(r.path, 'http://sink').pathname.endsWith('/batch/'));
      expect(batches.map((r) => (r.body as { api_key: string }).api_key)).toEqual(batches.map(() => apiKey));
      const sent2 = batches
        .flatMap((r) => (r.body as { batch: PosthogEvent[] }).batch.map((e) => e.properties.request_id));
      expect(sent2.sort()).toEqual(expected2);
      await page.reload();
      await expect(page.locator('.ag-row [col-id="status"]')).toHaveText(['success', 'success'], { timeout: UI_READY });
      await shot(page, testInfo, 'second-sync');
    }, { timeout: STEP.syncNow });

    await test.step('delete the connection', () =>
      deleteConnection(page, testInfo, actor, probe, connectionId, { name: renamed, destination: moved }, UI_READY),
    { timeout: STEP.remove });
    await test.step('the deleted connection\'s page says it is not found, once and inline', () =>
      expectDeletedConnectionPage(page, testInfo, connectionId, UI_READY), { timeout: STEP.deleted });
  },
);
