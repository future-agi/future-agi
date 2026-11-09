import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { inspectManagedMock } from '../../lib/managed-mock';
import { WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { EXPORT_CHANNELS, refusedCredential } from '../../lib/export-sinks';
import {
  browserLocalTime, CONNECTIONS_PATH, connectExport, deleteConnection, editCredentials, expectConnectionShown,
  expectedRequestIds, expectSyncApi, expectSyncHistory, firstSync, INTEGRATIONS_ROUTE, leaveConnected,
  mintRequestLogs, openPlatform, pressSync, shot, storedCredentials, SYNC_NOW_COOLDOWN_MS, syncAfter, syncLogsApi,
  validateCredentials, waitOutSyncCooldown,
} from '../../lib/integration-export';

test.skip(process.env.E2E_STACK === 'standalone', 'The export sinks are wired on the Distributed stack only');

// Pinned from integrations/services/datadog_service.py and
// activities.py _export_datadog: the connection check is GET
// /api/v1/validate with the DD-API-KEY header; the export posts the request
// logs to the log intake /api/v2/logs (one log each, up to 500 a batch:
// ddsource, service, hostname, message, ddtags, status, date, attributes)
// and the window's aggregate series to /api/v2/series ({ series: [...] },
// metric agentcc.gateway.*, tagged org:<id>), Datadog's documented intake
// APIs. The stack points both hosts at the webhook sink's datadog-export
// channel (INTEGRATIONS_DATADOG_URL, lib/export-sinks.ts), shared by every
// worker: this flow's requests are the ones carrying its minted API key. The
// connection stores and shows the chosen site's API host
// (integrations/services/export_destinations.py), here EU1's. Datadog's log intake
// keeps a log dated up to 18 hours back and drops older ones while answering
// 2xx, so the first sync covers the last 18 hours and an older start is
// refused (activities.py DATADOG_LOG_MAX_AGE, StepSyncSettings.jsx). A key
// Datadog refuses answers 403 at validate (datadog_service.py
// validate_credentials: the wizard's and Edit's refusal) and at the log
// intake (a failed sync: services/export_failures.py names it
// credentials_refused). The sink refuses a minted e2e-refused-* key, and a key
// revoked at it, on every route (stack/webhook-sink/server.mjs). A key that
// passes Edit's check makes a connection in Error active again
// (views/integration_connection.py _update_connection).
const BATCH_SIZE = 500;
const INTAKE_HOURS = 18;
const INTAKE_LIMIT = 'Datadog accepts logs from the last 18 hours. Pick a start within them.';
const DESTINATION = 'https://api.datadoghq.eu';
const REFUSED_KEY = 'Invalid API key. Please check your Datadog API key.';
const REFUSED_SYNC = 'Datadog refused the API key (HTTP 403). Replace it in Edit, then Sync Now.';
const UI_READY = 30_000;
// One Sync Now: the minute since the last attempt, the press, the job, and the reads after.
const PRESSED_SYNC = SYNC_NOW_COOLDOWN_MS + UI_READY + POLL.ASYNC_JOB.timeout + 30_000;
// Each step's budget; the test's ceiling is their sum.
const STEP = {
  credentials: 2 * UI_READY + 30_000, create: 60_000, sync: POLL.ASYNC_JOB.timeout, sink: 30_000,
  history: 60_000, shown: 60_000, revoked: PRESSED_SYNC, error: 60_000, edit: 2 * UI_READY + 30_000,
  recovered: PRESSED_SYNC, remove: 60_000,
};

interface DatadogLog {
  ddsource: string; service: string; hostname: string; message: string; ddtags: string;
  status: string; date: string; attributes: Record<string, unknown>;
}
interface DatadogSeries { metric: string; type: number; points: { timestamp: number; value: number }[]; tags: string[] }

test(
  'INT-E2E-002: gateway request logs and metrics export to Datadog',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'INT-E2E-002',
      area: 'integrations',
      userGoal:
        'An admin connects Datadog and finds the organization\'s gateway requests there as logs, with their request and error metrics',
      steps: [
        'deliver three gateway request logs, one of them a 500, and one 20 hours old, on the gateway logs webhook',
        'open Settings > Integrations and pick Datadog',
        'pick the EU1 site, enter an API key Datadog refuses, and Validate & Continue: read the refusal',
        'enter the API key, and Validate & Continue',
        'pick "Export from a specific date" 20 hours back and Connect Integration: read why it is refused',
        'go back to "Export the last 18 hours": the note is gone; Connect Integration, then Done',
        'open the connection and read its Sync History',
        'go Back to Integrations and read the connection\'s card under Connections, then its page header',
        'Datadog revokes the API key; deliver one more request log, wait out the minute and press Sync Now',
        'read the connection\'s Error badge, its reason and Retry',
        'Edit it with an API key Datadog refuses: read the refusal, then Edit it with a second API key',
        'wait out the minute and press Sync Now',
        'delete the connection from its page, cancelling once first',
      ],
      backendChecks: [
        'the managed stack is the attested mock stack, its API and worker sending Datadog only to the declared sink',
        'the refused key reached Datadog\'s validate endpoint once and was answered 403, and no create was sent',
        'the API key reached Datadog\'s validate endpoint twice (the validate step and the create); the refused start sent no create',
        'the first sync finished success in PG integrations_sync_log with the count of logs and of metric series it sent, over the last 18 hours',
        'the logs Datadog received at /api/v2/logs are exactly the org\'s request logs in the sync window, the three minted ones among them, and none older than 18 hours (the 20-hour-old one never sent)',
        'each minted request arrived with its tags and attributes, the 500 as status error with error:true',
        'the series at /api/v2/series count the window\'s requests and its errors for the org',
        'the sync-logs API and the connection detail agree: one success row with that count, the connection active',
        'the connection is stored with the EU1 site\'s API host and no FutureAGI project, and its card and page header show that host, its name and its exported count',
        'with the key revoked, Sync Now runs a sync whose logs the intake refuses (403): it fails in PG integrations_sync_log and the sync-logs API with error_details exactly { kind: credentials_refused, type: HTTPError, http_status: 403, vendor_code: null } and the plain reason, and the connection is in Error with that reason',
        'the Edit with the refused key answers 400 with Datadog\'s refusal and leaves the stored credentials as they were',
        'the Edit with the second key is checked at validate before it is stored: the stored credentials change and the connection is active with no reason',
        'the next sync succeeds and sends exactly the request logs since one minute before the last success (at-least-once deliveries, not unique requests), with the exact repeated ID set and cumulative delivery total checked, the one delivered during the revocation among them, to the log intake with the second key only',
        'deleting it soft-deletes it in PG and drops it from the connections API',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(Object.values(STEP).reduce((a, b) => a + b, 0));
    inspectManagedMock({ exportSinks: true });
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const minted = await mintRequestLogs(actor, probe, `e2e-int-dd-${suffix}`);
    const [tooOld] = await mintRequestLogs(actor, probe, `e2e-int-dd-old-${suffix}`, 1, 20);
    const apiKey = `e2e-dd-key-${suffix}`;
    const refusedKey = refusedCredential(`dd-${suffix}`);
    const secondKey = `e2e-dd-key2-${suffix}`;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: actor.organizationId, channel: EXPORT_CHANNELS.datadog,
        requestIds: minted.map((m) => m.request_id) }),
    });

    await test.step('connect Datadog', async () => {
      await openPlatform(page, 'Datadog', UI_READY);
      await page.getByLabel('Datadog Site').click();
      await page.getByRole('option', { name: 'EU1 (datadoghq.eu)' }).click();
      let creates = 0;
      const count = (r: { method(): string; url(): string }) => {
        if (r.method() === 'POST' && new URL(r.url()).pathname === CONNECTIONS_PATH) creates += 1;
      };
      page.on('request', count);
      await page.getByLabel('API Key').fill(refusedKey);
      expect(await validateCredentials(page, UI_READY)).toBe(400);
      await expect(page.getByRole('alert').filter({ hasText: REFUSED_KEY })).toHaveText([REFUSED_KEY],
        { timeout: UI_READY });
      page.off('request', count);
      expect(creates).toBe(0);
      await shot(page, testInfo, 'credentials-refused');
      await page.getByLabel('API Key').fill(apiKey);
      await shot(page, testInfo, 'credentials');
      expect(await validateCredentials(page, UI_READY)).toBe(200);
    }, { timeout: STEP.credentials });
    const connectionId = await test.step('connect the export', async () => {
      await expect(page.getByRole('radio', { name: /Export the last 18 hours/ })).toBeChecked({ timeout: UI_READY });
      const start = await browserLocalTime(page, INTAKE_HOURS + 2);
      await page.getByRole('radio', { name: 'Export from a specific date' }).check();
      await page.getByLabel('Start Date & Time').fill(start.value);
      await expect(page.getByText(INTAKE_LIMIT, { exact: true })).toHaveCount(1);
      let creates = 0;
      const count = (r: { method(): string; url(): string }) => {
        if (r.method() === 'POST' && new URL(r.url()).pathname === CONNECTIONS_PATH) creates += 1;
      };
      page.on('request', count);
      await page.getByRole('button', { name: 'Connect Integration' }).click();
      await expect(page.getByText(INTAKE_LIMIT, { exact: true })).toHaveCount(2);
      page.off('request', count);
      expect(creates).toBe(0);
      await shot(page, testInfo, 'intake-limit');
      await page.getByRole('radio', { name: /Export the last 18 hours/ }).check();
      await expect(page.getByText(INTAKE_LIMIT, { exact: true })).toHaveCount(0);
      await shot(page, testInfo, 'sync-settings');
      const id = await connectExport(page, UI_READY,
        { interval: 'Every 30 minutes', recentHours: INTAKE_HOURS, sends: { host_url: DESTINATION, display_name: 'Datadog' } });
      await shot(page, testInfo, 'connected');
      await leaveConnected(page, id, 'Done', UI_READY);
      return id;
    }, { timeout: STEP.create });
    await testInfo.attach('connection', { contentType: 'text/plain', body: connectionId });

    const sync = await firstSync(probe, connectionId);
    expect([sync.status, sync.error_message]).toEqual(['success', '']);
    const windowHours = (Date.parse(sync.sync_to) - Date.parse(sync.sync_from)) / 3600_000;
    expect(windowHours).toBeGreaterThan(INTAKE_HOURS - 0.1);
    expect(windowHours).toBeLessThanOrEqual(INTAKE_HOURS);
    const expected = await expectedRequestIds(probe, actor, sync);
    expect(expected).toEqual(expect.arrayContaining(minted.map((m) => m.request_id)));
    expect(expected).not.toContain(tooOld.request_id);
    expect(sync.traces_fetched).toBe(expected.length);

    expect(await probe.pg<{ host_url: string; project_id: string | null }>(
      'SELECT host_url, project_id FROM integrations_connection WHERE id = $1', [connectionId]))
      .toEqual([{ host_url: DESTINATION, project_id: null }]);

    const route = (path: string) =>
      new URL(path, 'http://sink').pathname.replace(`/r/${EXPORT_CHANNELS.datadog}/as/datadog`, '');
    const withKey = async (key: string) => (await new WebhookSink().received(EXPORT_CHANNELS.datadog))
      .filter((r) => r.headers['dd-api-key'] === key);
    expect((await withKey(refusedKey)).map((r) => `${r.method} ${route(r.path)} ${r.status}`))
      .toEqual(['GET /api/v1/validate 403']);
    await test.step('Datadog received exactly the window\'s request logs and its metrics', async () => {
      const mine = await withKey(apiKey);
      expect(mine.map((r) => `${r.method} ${route(r.path)} ${r.status}`)).toEqual([
        'GET /api/v1/validate 200', 'GET /api/v1/validate 200',
        ...Array(Math.ceil(expected.length / BATCH_SIZE)).fill('POST /api/v2/logs 202'),
        'POST /api/v2/series 202',
      ]);
      const logs = mine.filter((r) => route(r.path) === '/api/v2/logs').flatMap((r) => r.body as DatadogLog[]);
      expect(logs.map((l) => l.attributes.request_id).sort()).toEqual(expected);
      // Every log was at most 18 hours old when it reached the intake.
      for (const call of mine.filter((r) => route(r.path) === '/api/v2/logs')) {
        for (const log of call.body as DatadogLog[]) {
          expect(Date.parse(call.at) - Date.parse(log.date)).toBeLessThan(INTAKE_HOURS * 3600_000);
        }
      }
      for (const log of minted) {
        const failed = log.status_code >= 400;
        const sent = logs.find((l) => l.attributes.request_id === log.request_id)!;
        expect(sent).toMatchObject({
          ddsource: 'agentcc', service: 'agentcc-gateway', hostname: 'agentcc',
          status: failed ? 'error' : 'info',
          attributes: {
            model: log.model, provider: log.provider, status_code: log.status_code, is_error: failed,
            latency_ms: log.latency_ms, input_tokens: log.input_tokens, output_tokens: log.output_tokens,
            total_tokens: log.total_tokens, cost: Number(log.cost),
          },
        });
        const tags = sent.ddtags.split(',');
        expect(tags).toEqual(expect.arrayContaining([`model:${log.model}`, `provider:${log.provider}`,
          `status_code:${log.status_code}`]));
        expect(tags.includes('error:true')).toBe(failed);
        expect(Date.parse(sent.date)).toBe(Date.parse(log.timestamp));
      }

      const [seriesCall] = mine.filter((r) => route(r.path) === '/api/v2/series');
      const series = (seriesCall.body as { series: DatadogSeries[] }).series;
      expect(series.length).toBe(sync.spans_synced);
      const point = (metric: string) => {
        const found = series.filter((s) => s.metric === metric);
        expect(found.map((s) => s.tags)).toEqual([[`org:${actor.organizationId}`]]);
        return found[0].points.map((p) => p.value);
      };
      expect(point('agentcc.gateway.requests')).toEqual([expected.length]);
      expect(point('agentcc.gateway.errors')).toEqual([logs.filter((l) => l.status === 'error').length]);
    }, { timeout: STEP.sink });

    await test.step('the connection page shows the export', async () => {
      await expectSyncHistory(page, connectionId, expected.length, UI_READY);
      await shot(page, testInfo, 'sync-history');
    }, { timeout: STEP.history });
    await expectSyncApi(actor, connectionId, expected.length);
    const shown = { name: 'Datadog', destination: DESTINATION, platform: 'Datadog', exported: expected.length,
      interval: '30 min' };
    await test.step('the card and the page say where it delivers', () =>
      expectConnectionShown(page, testInfo, connectionId, shown, UI_READY), { timeout: STEP.shown });

    const sink = new WebhookSink();
    const conn = async () => (await probe.pg<{ status: string; status_message: string }>(
      `SELECT status, COALESCE(status_message, '') AS status_message FROM integrations_connection WHERE id = $1`,
      [connectionId]))[0];
    /** The connection page's status badge (StatusBadge.jsx), by its label. */
    const badge = (label: string) => page.locator('.MuiChip-root').filter({ hasText: new RegExp(`^${label}$`) });
    const failed = await test.step('Datadog revokes the key, and Sync Now fails with the reason', async () => {
      await sink.revoked(apiKey, true);
      const [late] = await mintRequestLogs(actor, probe, `e2e-int-dd-rev-${suffix}`, 1);
      await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
      await waitOutSyncCooldown(probe, connectionId);
      const pressed = await pressSync(page, connectionId, 'Sync Now', UI_READY);
      const sync = await syncAfter(probe, connectionId, pressed, 'failed');
      expect(sync.error_message).toBe(REFUSED_SYNC);
      const details = { kind: 'credentials_refused', type: 'HTTPError', http_status: 403, vendor_code: null };
      expect(sync.error_details).toEqual(details);
      const listed = (await syncLogsApi(actor, connectionId)).find((l) => l.id === sync.id);
      expect([listed?.status, listed?.error_message, listed?.error_details]).toEqual(['failed', REFUSED_SYNC, details]);
      expect(await conn()).toEqual({ status: 'error', status_message: REFUSED_SYNC });
      // The attempt reached the log intake with the revoked key and was refused.
      expect((await withKey(apiKey)).filter((r) => Date.parse(r.at) > pressed)
        .map((r) => `${r.method} ${route(r.path)} ${r.status}`)).toEqual(['POST /api/v2/logs 403']);
      return { sync, late };
    }, { timeout: STEP.revoked });
    await testInfo.attach('revoked-sync', { contentType: 'application/json',
      body: JSON.stringify({ syncLogId: failed.sync.id, requestId: failed.late.request_id }) });

    await test.step('the page says why, and offers Retry', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(badge('Error')).toHaveCount(1, { timeout: UI_READY });
      await expect(page.getByRole('alert').filter({ hasText: REFUSED_SYNC })).toHaveCount(1);
      await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled();
      await expect(page.locator('.ag-row [col-id="error_message"]').first()).toHaveText(REFUSED_SYNC,
        { timeout: UI_READY });
      await expect(page.getByText(/Client Error|for url|Traceback|dd-api-key/)).toHaveCount(0);
      await shot(page, testInfo, 'key-refused');
    }, { timeout: STEP.error });

    await test.step('Edit refuses a key Datadog refuses, and takes the second key', async () => {
      const before = await storedCredentials(probe, connectionId);
      const refused = await editCredentials(page, connectionId, 'Datadog', { 'API Key': refusedKey }, UI_READY);
      expect(refused).toEqual({ status: 400, reason: REFUSED_KEY });
      expect(await storedCredentials(probe, connectionId)).toBe(before);
      expect((await conn()).status).toBe('error');
      expect((await withKey(refusedKey)).map((r) => `${route(r.path)} ${r.status}`))
        .toEqual(['/api/v1/validate 403', '/api/v1/validate 403']);
      expect(await editCredentials(page, connectionId, 'Datadog', { 'API Key': secondKey }, UI_READY))
        .toEqual({ status: 200, reason: '' });
      expect(await storedCredentials(probe, connectionId)).not.toBe(before);
      expect((await withKey(secondKey)).map((r) => `${route(r.path)} ${r.status}`)).toEqual(['/api/v1/validate 200']);
      expect(await conn()).toEqual({ status: 'active', status_message: '' });
      await expect(badge('Active')).toHaveCount(1, { timeout: UI_READY });
      await expect(page.getByRole('alert').filter({ hasText: REFUSED_SYNC })).toHaveCount(0);
      await shot(page, testInfo, 'key-replaced');
    }, { timeout: STEP.edit });

    await test.step('Sync Now resumes the export with the second key', async () => {
      await waitOutSyncCooldown(probe, connectionId);
      const pressed = await pressSync(page, connectionId, 'Sync Now', UI_READY);
      const recovered = await syncAfter(probe, connectionId, pressed, 'success');
      expect(Date.parse(recovered.sync_from)).toBe(Date.parse(sync.sync_to) - 60_000);
      const since = await expectedRequestIds(probe, actor, recovered);
      expect(since).toContain(failed.late.request_id);
      expect(recovered.traces_fetched).toBe(since.length);
          const overlap = await probe.pg<{ request_id: string }>(
            `SELECT request_id FROM agentcc_request_log WHERE organization_id = $1
             AND request_id = ANY($2) AND COALESCE(started_at, created_at) >= $3::timestamptz - interval '1 minute'
             AND COALESCE(started_at, created_at) < $3::timestamptz ORDER BY request_id`,
            [actor.organizationId, expected, sync.sync_to]);
          const totals = await probe.pg<{ total: number }>(
            'SELECT total_traces_synced AS total FROM integrations_connection WHERE id = $1', [connectionId]);
          expect(Number(totals[0].total)).toBe(expected.length + since.length);
      const sent = (await withKey(secondKey)).filter((r) => Date.parse(r.at) > pressed);
      expect(sent.map((r) => `${r.method} ${route(r.path)} ${r.status}`)).toEqual([
        ...Array(Math.ceil(since.length / BATCH_SIZE)).fill('POST /api/v2/logs 202'), 'POST /api/v2/series 202',
      ]);
      const deliveredIds = sent.filter((r) => route(r.path) === '/api/v2/logs')
        .flatMap((r) => (r.body as DatadogLog[]).map((l) => String(l.attributes.request_id))).sort();
      expect(deliveredIds).toEqual(since);
      expect(deliveredIds.filter((id) => expected.includes(id))).toEqual(overlap.map((r) => r.request_id));
      expect((await withKey(apiKey)).filter((r) => Date.parse(r.at) > pressed)).toEqual([]);
      expect(await conn()).toEqual({ status: 'active', status_message: '' });
      await sink.revoked(apiKey, false);
    }, { timeout: STEP.recovered });
    await test.step('delete the connection', () =>
      deleteConnection(page, testInfo, actor, probe, connectionId, shown, UI_READY), { timeout: STEP.remove });
  },
);
