import { createHash } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { inspectManagedMock } from '../../lib/managed-mock';
import { WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { EXPORT_CHANNELS, refusedCredential } from '../../lib/export-sinks';
import {
  browserLocalTime, CONNECTIONS_PATH, connectExport, deleteConnection, editCredentials, expectConnectionShown,
  expectedRequestIds, expectSyncApi, expectSyncHistory, firstSync, INTEGRATIONS_ROUTE, mintRequestLogs,
  openPlatform, pressSync, shot, storedCredentials, SYNC_NOW_COOLDOWN_MS, syncAfter, syncLogsApi,
  validateCredentials, waitOutSyncCooldown,
} from '../../lib/integration-export';

test.skip(process.env.E2E_STACK === 'standalone', 'The export sinks are wired on the Distributed stack only');

// Pinned from integrations/services/mixpanel_service.py. With a project token
// only, the connection check and the export post a JSON array of events to
// /track (https://docs.mixpanel.com/reference/track-event: it answers the
// text 1 when it takes the batch, 0 when it takes none, and accepts the last
// 5 days); with the project's API secret they post to /import?strict=1 with
// the secret as the Basic auth username
// (https://docs.mixpanel.com/reference/import-events). Each event is
// { event, properties: { token, distinct_id, time, $insert_id, ... } }, the
// $insert_id the first 32 hex of the request id's SHA-256; one
// agentcc_request event per request log (integrations/schemas.py
// ExportEvent), up to 500 per batch (activities.py _batch_export). The stack
// points the host at the webhook sink's mixpanel-export channel
// (INTEGRATIONS_MIXPANEL_URL, lib/export-sinks.ts), which answers as Mixpanel
// documents both routes and is shared by every worker: a flow's requests are
// the ones whose events carry its minted token. /track answers 0 to a token
// the sink refuses (a minted e2e-refused-* one, or one revoked at it): the
// wizard and Edit read that as a refused token (mixpanel_service.py
// validate_credentials), a sync as Mixpanel taking none of its events
// (MixpanelRejected, services/export_failures.py events_refused). A token that
// passes Edit's check makes a connection in Error active again
// (views/integration_connection.py _update_connection).
const BATCH_SIZE = 500;
const DESTINATION = 'https://api.mixpanel.com';
const REFUSED_TOKEN = 'Invalid project token. Please check your Mixpanel project token.';
const REFUSED_SYNC =
  'Mixpanel accepted none of the request logs in a batch (check the project token). Replace it in Edit, then Sync Now.';
const UI_READY = 30_000;
// One Sync Now: the minute since the last attempt, the press, the job, and the reads after.
const PRESSED_SYNC = SYNC_NOW_COOLDOWN_MS + UI_READY + POLL.ASYNC_JOB.timeout + 30_000;
// Each step's budget; a test's ceiling is their sum (the recovery steps run for /track only).
const STEP = {
  credentials: 60_000, create: 60_000, sync: POLL.ASYNC_JOB.timeout, sink: 30_000,
  history: 60_000, shown: 60_000, remove: 60_000,
};
const TRACK_STEP = {
  refusedToken: UI_READY + 30_000, revoked: PRESSED_SYNC, error: 60_000, edit: UI_READY + 30_000,
  recovered: PRESSED_SYNC,
};
const sum = (steps: Record<string, number>) => Object.values(steps).reduce((a, b) => a + b, 0);
const TRACK_LIMIT =
  'Without an API secret, Mixpanel accepts request logs from the last 5 days. Add the API secret to export older ones.';

interface MixpanelEvent { event: string; properties: Record<string, unknown> }
const insertId = (requestId: string) => createHash('sha256').update(requestId).digest('hex').slice(0, 32);

for (const mode of ['track', 'import'] as const) {
  const imports = mode === 'import';
  test(
    imports
      ? 'INT-E2E-005: gateway request logs import into Mixpanel with the API secret'
      : 'INT-E2E-003: gateway request logs export to Mixpanel',
    {
      tag: ['@flow'],
      annotation: flowAnnotation({
        id: imports ? 'INT-E2E-005' : 'INT-E2E-003',
        area: 'integrations',
        userGoal: imports
          ? 'An admin connects Mixpanel with the project\'s API secret and finds the organization\'s gateway requests imported there as events'
          : 'An admin connects Mixpanel and finds the organization\'s gateway requests there as events',
        steps: [
          'deliver three gateway request logs, one of them a 500, on the gateway logs webhook',
          'open Settings > Integrations and pick Mixpanel',
          ...(imports
            ? ['enter a project token and the API secret, and Validate & Continue']
            : ['enter a project token Mixpanel refuses, and Validate & Continue: read the refusal',
              'enter a project token, and Validate & Continue']),
          ...(imports
            ? ['keep "Export the last 24 hours" and Connect Integration']
            : ['pick "Export from a specific date" 6 days back and Connect Integration: read the 5-day limit',
              'start it 3 hours back instead and Connect Integration']),
          'open the connection and read its Sync History',
          'go Back to Integrations and read the connection\'s card under Connections, then its page header',
          ...(imports ? [] : [
            'Mixpanel revokes the token; deliver one more request log, wait out the minute and press Sync Now',
            'read the connection\'s Error badge, its reason and Retry',
            'Edit it with a second project token',
            'wait out the minute and press Sync Now',
          ]),
          'delete the connection from its page, cancelling once first',
        ],
        backendChecks: [
          'the managed stack is the attested mock stack, its API and worker sending Mixpanel only to the declared sink',
          ...(imports ? [] : [
            'the refused token reached /track once as a connection-test event and was answered 0, and no create was sent',
          ]),
          imports
            ? 'the connection check reached /import?strict=1 twice (the validate step and the create) with the secret as Basic auth'
            : 'the token reached /track twice as a connection-test event (the validate step and the create), and the 6-day start was never sent',
          imports
            ? 'the first sync finished success in PG integrations_sync_log with the count of request logs it sent'
            : 'the first sync started at the chosen time and finished success in PG integrations_sync_log with the count of request logs it sent',
          `the agentcc_request events Mixpanel received at ${imports ? '/import?strict=1, with the secret as Basic auth,' : '/track'} are exactly the org's request logs in the sync window, the three minted ones among them`,
          'each minted request arrived with the token, its $insert_id, model, provider, status, tokens and time',
          'the sync-logs API and the connection detail agree: one success row with that count, the connection active',
          'the connection is stored with https://api.mixpanel.com and no FutureAGI project, and its card and page header show it, its name and its exported count',
          ...(imports ? [] : [
            'with the token revoked, Sync Now runs a sync whose /track answers 0: it fails in PG integrations_sync_log and the sync-logs API with error_details exactly { kind: events_refused, type: MixpanelRejected, http_status: null, vendor_code: null } and the plain reason, counts nothing exported (traces_fetched 0, the connection\'s exported total unchanged), and the connection is in Error with that reason',
            'the Edit with the second token is checked with /track before it is stored: the stored credentials change and the connection is active with no reason',
            'the next sync succeeds and sends exactly the request logs since one minute before the last success (at-least-once deliveries, not unique requests), with the exact repeated ID set and cumulative delivery total checked, the one delivered during the revocation among them, to /track with the second token only, each batch answered 1',
          ]),
          'deleting it soft-deletes it in PG and drops it from the connections API',
        ],
      }),
    },
    async ({ page, actor, probe }, testInfo) => {
      test.setTimeout(sum(STEP) + (imports ? 0 : sum(TRACK_STEP)));
      inspectManagedMock({ exportSinks: true });
      const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
      const minted = await mintRequestLogs(actor, probe, `e2e-int-mp-${mode}-${suffix}`);
      const token = `e2e-mp-token-${mode}-${suffix}`;
      const secret = `e2e-mp-secret-${suffix}`;
      const refusedToken = refusedCredential(`mp-${suffix}`);
      const secondToken = `e2e-mp-token2-${suffix}`;
      await testInfo.attach('seeded', {
        contentType: 'application/json',
        body: JSON.stringify({ organizationId: actor.organizationId, channel: EXPORT_CHANNELS.mixpanel,
          requestIds: minted.map((m) => m.request_id) }),
      });

      await test.step('connect Mixpanel', async () => {
        await openPlatform(page, 'Mixpanel', UI_READY);
        if (!imports) {
          let creates = 0;
          const count = (r: { method(): string; url(): string }) => {
            if (r.method() === 'POST' && new URL(r.url()).pathname === CONNECTIONS_PATH) creates += 1;
          };
          page.on('request', count);
          await page.getByLabel('Project Token').fill(refusedToken);
          expect(await validateCredentials(page, UI_READY)).toBe(400);
          await expect(page.getByRole('alert').filter({ hasText: REFUSED_TOKEN })).toHaveText([REFUSED_TOKEN],
            { timeout: UI_READY });
          page.off('request', count);
          expect(creates).toBe(0);
          await shot(page, testInfo, 'credentials-refused');
        }
        await page.getByLabel('Project Token').fill(token);
        if (imports) await page.getByLabel('API Secret (Optional)').fill(secret);
        await shot(page, testInfo, 'credentials');
        expect(await validateCredentials(page, UI_READY)).toBe(200);
      }, { timeout: STEP.credentials + (imports ? 0 : TRACK_STEP.refusedToken) });

      let start: { value: string; ms: number } | undefined;
      const connectionId = await test.step('connect the export', async () => {
        if (!imports) {
          const tooOld = await browserLocalTime(page, 6 * 24);
          await page.getByRole('radio', { name: 'Export from a specific date' }).check();
          await page.getByLabel('Start Date & Time').fill(tooOld.value);
          await expect(page.getByText(TRACK_LIMIT, { exact: true })).toHaveCount(1);
          let sent = 0;
          const count = () => { sent += 1; };
          page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/integrations/connections/')) count(); });
          await page.getByRole('button', { name: 'Connect Integration' }).click();
          await expect(page.getByText(TRACK_LIMIT, { exact: true })).toHaveCount(2);
          expect(sent).toBe(0);
          await shot(page, testInfo, 'track-limit');
          await page.getByRole('radio', { name: /Export the last 24 hours/ }).check();
          start = await browserLocalTime(page, 3);
        }
        await shot(page, testInfo, 'sync-settings');
        const id = await connectExport(page, UI_READY,
          { interval: 'Every 30 minutes', fromDate: start?.value, sends: { host_url: DESTINATION, display_name: 'Mixpanel' } });
        await shot(page, testInfo, 'connected');
        return id;
      }, { timeout: STEP.create });
      await testInfo.attach('connection', { contentType: 'text/plain', body: connectionId });

      const sync = await firstSync(probe, connectionId);
      expect([sync.status, sync.error_message]).toEqual(['success', '']);
      if (start) {
        const [first] = await probe.pg<{ ms: string }>(
          `SELECT (extract(epoch FROM sync_from) * 1000)::bigint::text AS ms FROM integrations_sync_log
            WHERE connection_id = $1 ORDER BY started_at LIMIT 1`, [connectionId]);
        expect(Number(first.ms)).toBe(start.ms);
      }
      const expected = await expectedRequestIds(probe, actor, sync);
      expect(expected).toEqual(expect.arrayContaining(minted.map((m) => m.request_id)));
      expect(sync.traces_fetched).toBe(expected.length);
      expect(await probe.pg<{ host_url: string; project_id: string | null }>(
        'SELECT host_url, project_id FROM integrations_connection WHERE id = $1', [connectionId]))
        .toEqual([{ host_url: DESTINATION, project_id: null }]);

      const at = (path: string) => {
        const url = new URL(path, 'http://sink');
        return `${url.pathname.replace(`/r/${EXPORT_CHANNELS.mixpanel}/as/mixpanel`, '')}${url.search}`;
      };
      const withToken = async (t: string) => (await new WebhookSink().received(EXPORT_CHANNELS.mixpanel)).filter((r) =>
        Array.isArray(r.body) && (r.body as MixpanelEvent[]).some((e) => e.properties?.token === t));
      if (!imports) {
        // One connection-test event with the refused token (the sink answered it 0; the wizard read that as
        // the refusal above), and no create.
        const refused = await withToken(refusedToken);
        expect(refused.map((r) => `${r.method} ${at(r.path)} ${r.status}`)).toEqual(['POST /track 200']);
        expect((refused[0].body as MixpanelEvent[]).map((e) => e.event)).toEqual(['$agentcc_connection_test']);
      }
      await test.step(`Mixpanel received exactly the window's request logs at /${mode}`, async () => {
        const mine = await withToken(token);
        const kinds = mine.map((r) => [...new Set((r.body as MixpanelEvent[]).map((e) => e.event))].join(','));
        const route = imports ? '/import?strict=1' : '/track';
        expect(mine.map((r, i) => `${r.method} ${at(r.path)} ${r.status} ${kinds[i]}`)).toEqual([
          `POST ${route} 200 $agentcc_connection_test`, `POST ${route} 200 $agentcc_connection_test`,
          ...Array(Math.ceil(expected.length / BATCH_SIZE)).fill(`POST ${route} 200 agentcc_request`),
        ]);
        const basic = `Basic ${Buffer.from(`${secret}:`).toString('base64')}`;
        expect(mine.map((r) => r.headers.authorization)).toEqual(mine.map(() => (imports ? basic : undefined)));
        const events = mine.slice(2).flatMap((r) => r.body as MixpanelEvent[]);
        expect(events.map((e) => e.properties.token)).toEqual(events.map(() => token));
        expect(events.map((e) => e.properties.request_id).sort()).toEqual(expected);
        for (const log of minted) {
          const event = events.find((e) => e.properties.request_id === log.request_id)!;
          expect(event.properties).toMatchObject({
            distinct_id: 'agentcc-gateway', model: log.model, provider: log.provider,
            status_code: log.status_code, is_error: log.status_code >= 400, total_tokens: log.total_tokens,
            latency_ms: log.latency_ms, cost: Number(log.cost), $insert_id: insertId(log.request_id),
            time: Math.floor(Date.parse(log.timestamp) / 1000),
          });
        }
      }, { timeout: STEP.sink });

      await test.step('the connection page shows the export', async () => {
        await expectSyncHistory(page, connectionId, expected.length, UI_READY);
        await shot(page, testInfo, 'sync-history');
      }, { timeout: STEP.history });
      await expectSyncApi(actor, connectionId, expected.length);
      const shown = { name: 'Mixpanel', destination: DESTINATION, platform: 'Mixpanel', exported: expected.length,
        interval: '30 min' };
      await test.step('the card and the page say where it delivers', () =>
        expectConnectionShown(page, testInfo, connectionId, shown, UI_READY), { timeout: STEP.shown });

      if (!imports) {
        const sink = new WebhookSink();
        const conn = async () => (await probe.pg<{ status: string; status_message: string; total: number }>(
          `SELECT status, COALESCE(status_message, '') AS status_message, total_traces_synced AS total
             FROM integrations_connection WHERE id = $1`, [connectionId]))[0];
        const badge = (label: string) => page.locator('.MuiChip-root').filter({ hasText: new RegExp(`^${label}$`) });
        const tracked = (calls: Awaited<ReturnType<typeof withToken>>) =>
          calls.flatMap((r) => (r.body as MixpanelEvent[]).filter((e) => e.event === 'agentcc_request'));
        const failed = await test.step('Mixpanel revokes the token, and Sync Now fails with the reason', async () => {
          await sink.revoked(token, true);
          const [late] = await mintRequestLogs(actor, probe, `e2e-int-mp-rev-${suffix}`, 1);
          const exported = (await conn()).total;
          await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
          await waitOutSyncCooldown(probe, connectionId);
          const pressed = await pressSync(page, connectionId, 'Sync Now', UI_READY);
          const sync = await syncAfter(probe, connectionId, pressed, 'failed');
          expect([sync.error_message, sync.traces_fetched]).toEqual([REFUSED_SYNC, 0]);
          const details = { kind: 'events_refused', type: 'MixpanelRejected', http_status: null, vendor_code: null };
          expect(sync.error_details).toEqual(details);
          const listed = (await syncLogsApi(actor, connectionId)).find((l) => l.id === sync.id);
          expect([listed?.status, listed?.traces_fetched, listed?.error_message, listed?.error_details])
            .toEqual(['failed', 0, REFUSED_SYNC, details]);
          expect(await conn()).toEqual({ status: 'error', status_message: REFUSED_SYNC, total: exported });
          // The batch reached /track with the revoked token and was taken by none of its events.
          const sent = (await withToken(token)).filter((r) => Date.parse(r.at) > pressed);
          expect(sent.map((r) => `${r.method} ${at(r.path)} ${r.status}`)).toEqual(['POST /track 200']);
          expect(tracked(sent).map((e) => e.properties.request_id)).toContain(late.request_id);
          return { sync, late };
        }, { timeout: TRACK_STEP.revoked });
        await testInfo.attach('revoked-sync', { contentType: 'application/json',
          body: JSON.stringify({ syncLogId: failed.sync.id, requestId: failed.late.request_id }) });

        await test.step('the page says why, and offers Retry', async () => {
          await page.reload({ waitUntil: 'domcontentloaded' });
          await expect(badge('Error')).toHaveCount(1, { timeout: UI_READY });
          await expect(page.getByRole('alert').filter({ hasText: REFUSED_SYNC })).toHaveCount(1);
          await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled();
          await expect(page.locator('.ag-row [col-id="error_message"]').first()).toHaveText(REFUSED_SYNC,
            { timeout: UI_READY });
          await expect(page.getByText(/MixpanelRejected|it answered|Traceback/)).toHaveCount(0);
          await shot(page, testInfo, 'token-refused');
        }, { timeout: TRACK_STEP.error });

        await test.step('Edit takes the second token', async () => {
          const before = await storedCredentials(probe, connectionId);
          expect(await editCredentials(page, connectionId, 'Mixpanel', { 'Project Token': secondToken }, UI_READY))
            .toEqual({ status: 200, reason: '' });
          expect(await storedCredentials(probe, connectionId)).not.toBe(before);
          expect((await withToken(secondToken)).map((r) => `${at(r.path)} ${r.status}`)).toEqual(['/track 200']);
          expect((await conn()).status).toBe('active');
          expect((await conn()).status_message).toBe('');
          await expect(badge('Active')).toHaveCount(1, { timeout: UI_READY });
          await shot(page, testInfo, 'token-replaced');
        }, { timeout: TRACK_STEP.edit });

        await test.step('Sync Now resumes the export with the second token', async () => {
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
          const sent = (await withToken(secondToken)).filter((r) => Date.parse(r.at) > pressed);
          expect(sent.map((r) => `${r.method} ${at(r.path)} ${r.status}`))
            .toEqual(Array(Math.ceil(since.length / BATCH_SIZE)).fill('POST /track 200'));
          expect(tracked(sent).map((e) => e.properties.token)).toEqual(tracked(sent).map(() => secondToken));
          const deliveredIds = tracked(sent).map((e) => String(e.properties.request_id)).sort();
          expect(deliveredIds).toEqual(since);
          expect(deliveredIds.filter((id) => expected.includes(id))).toEqual(overlap.map((r) => r.request_id));
          expect((await withToken(token)).filter((r) => Date.parse(r.at) > pressed)).toEqual([]);
          expect(await conn()).toMatchObject({ status: 'active', status_message: '' });
          await sink.revoked(token, false);
        }, { timeout: TRACK_STEP.recovered });
      }
      await test.step('delete the connection', () =>
        deleteConnection(page, testInfo, actor, probe, connectionId, shown, UI_READY), { timeout: STEP.remove });
    },
  );
}
