import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { inspectManagedMock } from '../../lib/managed-mock';
import { POLL } from '../../lib/state-probe';
import { StorageSink, type StorageUser } from '../../lib/export-sinks';
import {
  connectExport, deleteConnection, editCredentials, expectConnectionShown, expectedRequestIds, expectSyncApi,
  expectSyncHistory, firstSync, INTEGRATIONS_ROUTE, leaveConnected, mintRequestLogs, openPlatform, pressSync, shot,
  storedCredentials, SYNC_NOW_COOLDOWN_MS, syncAfter, syncLogsApi, validateCredentials, waitOutSyncCooldown,
} from '../../lib/integration-export';

test.skip(process.env.E2E_STACK === 'standalone', 'The export sinks are wired on the Distributed stack only');

// Pinned from integrations/services/cloud_storage_service.py: the connection
// check writes and deletes <prefix>/_agentcc_test.txt; the export writes one
// gzip JSON Lines object per batch of up to 500 request logs (activities.py
// _batch_export) at <prefix>/logs/YYYY/MM/DD/hour=HH/batch_<12 hex>.jsonl.gz,
// Content-Type application/x-ndjson, Content-Encoding gzip, one
// integrations/schemas.py ExportLogRecord per line. The stack points Amazon S3
// at the storage sink (INTEGRATIONS_S3_ENDPOINT_URL, lib/export-sinks.ts);
// this flow's objects are the ones under the key prefix it minted. The
// connection stores the bucket in its region under the prefix
// (integrations/services/export_destinations.py) and shows it as
// s3://<bucket>/<prefix> in <region>; the flow picks a region other than the
// preselected us-east-1, so the region it shows is the one chosen. Edit
// replaces the access key pair after checking it with S3 the way the wizard
// does (views/integration_connection.py _update_connection; its note names the
// platform, Cloud Storage, EditIntegrationDialog.jsx). The second key
// pair is a disposable storage-sink user allowed only to put and delete
// objects under the flow's prefix (lib/export-sinks.ts StorageSink.addUser);
// once it is removed, S3 answers its key InvalidAccessKeyId (403), which a
// sync names credentials_refused (services/export_failures.py), while the
// sink's own keys still work: only the pair the connection holds fails.
const BATCH_SIZE = 500;
const REGION = 'eu-west-1';
const UI_READY = 30_000;
// One Sync Now: the minute since the last attempt, the press, the job, and the reads after.
const PRESSED_SYNC = SYNC_NOW_COOLDOWN_MS + UI_READY + POLL.ASYNC_JOB.timeout + 30_000;
// Each step's budget; the test's ceiling is their sum.
const STEP = {
  credentials: 90_000, create: 60_000, sync: POLL.ASYNC_JOB.timeout, sink: 60_000,
  history: 60_000, shown: 60_000, refusedEdit: UI_READY + 30_000, replace: UI_READY + 60_000,
  recovered: PRESSED_SYNC + 30_000, removed: PRESSED_SYNC, remove: 60_000,
};
const RECORD_FIELDS = ['cache_hit', 'cost', 'error_message', 'event_type', 'fallback_used', 'guardrail_triggered',
  'input_tokens', 'is_error', 'is_stream', 'latency_ms', 'model', 'output_tokens', 'provider', 'request_id',
  'resolved_model', 'routing_strategy', 'status_code', 'timestamp', 'total_tokens'];

test(
  'INT-E2E-004: gateway request logs export to Amazon S3',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'INT-E2E-004',
      area: 'integrations',
      userGoal:
        'An admin connects an S3 bucket and finds the organization\'s gateway requests archived there as JSON Lines',
      steps: [
        'deliver three gateway request logs, one of them a 500, on the gateway logs webhook',
        'open Settings > Integrations and pick Cloud Storage, which offers Amazon S3',
        'enter the bucket, the access key with a wrong secret, and Validate & Continue: read the refusal',
        'enter the right secret, pick the eu-west-1 region, enter a key prefix, and Validate & Continue',
        'pick "Every 30 minutes", keep "Export the last 24 hours" and Connect Integration, then View Integration',
        'read the connection\'s Sync History',
        'go Back to Integrations and read the connection\'s card under Connections, then its page header',
        'Edit it with the access key and a wrong secret: read the refusal',
        'Edit it with a second storage user\'s key pair, allowed only under the prefix',
        'deliver one more request log, wait out the minute and press Sync Now',
        'remove that user from the storage, deliver one more request log, wait out the minute and press Sync Now: read the refusal',
        'delete the connection from its page, cancelling once first',
      ],
      backendChecks: [
        'the managed stack is the attested mock stack, its API and worker writing S3 only to the declared storage sink',
        'the first sync finished success in PG integrations_sync_log with the count of request logs it sent',
        'the objects under the prefix are exactly the batch objects, gzip JSON Lines at the dated key layout, the connection test object removed',
        'their records are exactly the org\'s request logs in the sync window, the three minted ones among them, each with every export field',
        'the minted 500 arrived as is_error with its error message',
        'the sync-logs API and the connection detail agree: one success row with that count, the connection active',
        'the connection is stored with the bucket\'s address in its region under the prefix, an 1800 s interval and no FutureAGI project, and its card and page header show s3://<bucket>/<prefix> in <region>, its name, its interval and its exported count',
        'the Edit with a wrong secret answers 400 with S3\'s SignatureDoesNotMatch and leaves the stored credentials as they were',
        'the Edit with the second user\'s key pair is checked with S3 before it is stored: the stored credentials change',
        'the next sync succeeds and writes exactly the request logs since one minute before the last success (at-least-once deliveries, not unique requests), with the exact repeated ID set and cumulative delivery total checked, the new one among them, as new batch objects under the prefix',
        'with that user removed (the sink\'s own keys still valid), the next sync fails in PG integrations_sync_log and the sync-logs API with error_details exactly { kind: credentials_refused, type: ClientError, http_status: 403, vendor_code: InvalidAccessKeyId }: the connection was delivering with the second key pair',
        'the second user and its policy are gone from the storage sink',
        'deleting it soft-deletes it in PG and drops it from the connections API',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(Object.values(STEP).reduce((a, b) => a + b, 0));
    inspectManagedMock({ exportSinks: true });
    const sink = new StorageSink();
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const label = `e2e-int-s3-${suffix}`;
    const minted = await mintRequestLogs(actor, probe, label);
    const prefix = `e2e-s3-${suffix}`;
    const storedAt = `https://${sink.bucket}.s3.${REGION}.amazonaws.com/${prefix}`;
    const destination = `s3://${sink.bucket}/${prefix} in ${REGION}`;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: actor.organizationId, bucket: sink.bucket, prefix,
        requestIds: minted.map((m) => m.request_id) }),
    });

    await test.step('connect an S3 bucket', async () => {
      await openPlatform(page, 'Cloud Storage', UI_READY);
      // Amazon S3 is the one storage choice offered, and preselected.
      await expect(page.getByText('Amazon S3', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(/Azure Blob|Google Cloud Storage/)).toHaveCount(0);
      await page.getByLabel('Bucket Name').fill(sink.bucket);
      await page.getByLabel('Access Key ID').fill(sink.accessKeyId);
      await page.getByLabel('Secret Access Key').fill(`${sink.secretAccessKey}-wrong`);
      expect(await validateCredentials(page, UI_READY)).toBe(400);
      await expect(page.getByText(/^S3 error \(SignatureDoesNotMatch\): /)).toBeVisible({ timeout: UI_READY });
      await shot(page, testInfo, 'credentials-refused');
      await page.getByLabel('Secret Access Key').fill(sink.secretAccessKey);
      await page.getByLabel('Region').click();
      await page.getByRole('option', { name: REGION, exact: true }).click();
      // The select itself (its closing option list carries the same label).
      await expect(page.getByRole('combobox', { name: 'Region' })).toHaveText(REGION);
      await page.getByLabel('Key Prefix (Optional)').fill(prefix);
      await shot(page, testInfo, 'credentials');
      expect(await validateCredentials(page, UI_READY)).toBe(200);
    }, { timeout: STEP.credentials });
    const connectionId = await test.step('connect the export', async () => {
      await shot(page, testInfo, 'sync-settings');
      const id = await connectExport(page, UI_READY, {
        interval: 'Every 30 minutes', sends: { host_url: storedAt, display_name: 'Amazon S3' },
      });
      await shot(page, testInfo, 'connected');
      await leaveConnected(page, id, 'View Integration', UI_READY);
      return id;
    }, { timeout: STEP.create });
    await testInfo.attach('connection', { contentType: 'text/plain', body: connectionId });

    const sync = await firstSync(probe, connectionId);
    expect([sync.status, sync.error_message]).toEqual(['success', '']);
    const expected = await expectedRequestIds(probe, actor, sync);
    expect(expected).toEqual(expect.arrayContaining(minted.map((m) => m.request_id)));
    expect(sync.traces_fetched).toBe(expected.length);

    expect(await probe.pg<{ host_url: string; project_id: string | null; sync_interval_seconds: number }>(
      'SELECT host_url, project_id, sync_interval_seconds FROM integrations_connection WHERE id = $1',
      [connectionId])).toEqual([{ host_url: storedAt, project_id: null, sync_interval_seconds: 1800 }]);

    await test.step('the bucket holds exactly the window\'s request logs', async () => {
      const objects = sink.objects(prefix);
      await testInfo.attach('objects', { contentType: 'application/json',
        body: JSON.stringify(objects.map((o) => ({ key: o.key, size: o.size, records: o.records.length }))) });
      const layout = new RegExp(`^${prefix}/logs/\\d{4}/\\d{2}/\\d{2}/hour=\\d{2}/batch_[0-9a-f]{12}\\.jsonl\\.gz$`);
      expect(objects.map((o) => layout.test(o.key))).toEqual(
        Array(Math.ceil(expected.length / BATCH_SIZE)).fill(true));
      expect(objects.map((o) => [o.contentType, o.contentEncoding])).toEqual(
        objects.map(() => ['application/x-ndjson', 'gzip']));
      const records = objects.flatMap((o) => o.records);
      expect(records.map((r) => r.request_id).sort()).toEqual(expected);
      expect(records.map((r) => Object.keys(r).sort())).toEqual(records.map(() => RECORD_FIELDS));
      for (const log of minted) {
        const failed = log.status_code >= 400;
        const record = records.find((r) => r.request_id === log.request_id)!;
        expect(record).toMatchObject({
          model: log.model, provider: log.provider, status_code: log.status_code, is_error: failed,
          latency_ms: log.latency_ms, input_tokens: log.input_tokens, output_tokens: log.output_tokens,
          total_tokens: log.total_tokens, cost: Number(log.cost), event_type: 'request',
          error_message: failed ? `${label} upstream failed` : '',
        });
        expect(Date.parse(record.timestamp as string)).toBe(Date.parse(log.timestamp));
      }
    }, { timeout: STEP.sink });

    await test.step('the connection page shows the export', async () => {
      await expectSyncHistory(page, connectionId, expected.length, UI_READY);
      await shot(page, testInfo, 'sync-history');
    }, { timeout: STEP.history });
    await expectSyncApi(actor, connectionId, expected.length);
    const shown = { name: 'Amazon S3', destination, platform: 'Cloud Storage', exported: expected.length,
      interval: '30 min' };
    await test.step('the card and the page say where it delivers', () =>
      expectConnectionShown(page, testInfo, connectionId, shown, UI_READY), { timeout: STEP.shown });

    const keyPairRefused = 'Amazon S3 refused the access keys (InvalidAccessKeyId). Replace them in Edit, then Sync Now.';
    const status = async () => (await probe.pg<{ status: string; status_message: string }>(
      `SELECT status, COALESCE(status_message, '') AS status_message FROM integrations_connection WHERE id = $1`,
      [connectionId]))[0];
    const keys = (objects: { key: string }[]) => objects.map((o) => o.key);
    const before = await storedCredentials(probe, connectionId);
    await test.step('Edit refuses a wrong secret and keeps the stored keys', async () => {
      await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
      const refused = await editCredentials(page, connectionId, 'Cloud Storage',
        { 'Secret Access Key': `${sink.secretAccessKey}-wrong` }, UI_READY);
      expect(refused.status).toBe(400);
      expect(refused.reason).toMatch(/^S3 error \(SignatureDoesNotMatch\): /);
      expect(await storedCredentials(probe, connectionId)).toBe(before);
      expect(await status()).toEqual({ status: 'active', status_message: '' });
      await shot(page, testInfo, 'edit-refused');
    }, { timeout: STEP.refusedEdit });

    let user: StorageUser | undefined;
    try {
      user = sink.addUser(`e2e-s3u-${suffix}`.slice(0, 20), prefix);
      const second = user;
      await testInfo.attach('storage-user', { contentType: 'text/plain', body: second.accessKeyId });
      await test.step('Edit takes the second user\'s key pair', async () => {
        expect(sink.users()).toContain(second.accessKeyId);
        expect(await editCredentials(page, connectionId, 'Cloud Storage',
          { 'Access Key ID': second.accessKeyId, 'Secret Access Key': second.secretAccessKey }, UI_READY))
          .toEqual({ status: 200, reason: '' });
        expect(await storedCredentials(probe, connectionId)).not.toBe(before);
        // The check's test object was written and removed again.
        expect(keys(sink.objects(prefix)).filter((k) => k.endsWith('_agentcc_test.txt'))).toEqual([]);
        expect(await status()).toEqual({ status: 'active', status_message: '' });
      }, { timeout: STEP.replace });

      const delivered = await test.step('Sync Now writes with the second key pair', async () => {
        const [late] = await mintRequestLogs(actor, probe, `${label}-new`, 1);
        const earlier = new Set(keys(sink.objects(prefix)));
        await waitOutSyncCooldown(probe, connectionId);
        const pressed = await pressSync(page, connectionId, 'Sync Now', UI_READY);
        const recovered = await syncAfter(probe, connectionId, pressed, 'success');
        expect(Date.parse(recovered.sync_from)).toBe(Date.parse(sync.sync_to) - 60_000);
        const since = await expectedRequestIds(probe, actor, recovered);
        expect(since).toContain(late.request_id);
        expect(recovered.traces_fetched).toBe(since.length);
          const overlap = await probe.pg<{ request_id: string }>(
            `SELECT request_id FROM agentcc_request_log WHERE organization_id = $1
             AND request_id = ANY($2) AND COALESCE(started_at, created_at) >= $3::timestamptz - interval '1 minute'
             AND COALESCE(started_at, created_at) < $3::timestamptz ORDER BY request_id`,
            [actor.organizationId, expected, sync.sync_to]);
          const totals = await probe.pg<{ total: number }>(
            'SELECT total_traces_synced AS total FROM integrations_connection WHERE id = $1', [connectionId]);
          expect(Number(totals[0].total)).toBe(expected.length + since.length);
        const added = sink.objects(prefix).filter((o) => !earlier.has(o.key));
        expect(added.length).toBe(Math.ceil(since.length / BATCH_SIZE));
        const deliveredIds = added.flatMap((o) => o.records.map((r) => String(r.request_id))).sort();
        expect(deliveredIds).toEqual(since);
        expect(deliveredIds.filter((id) => expected.includes(id))).toEqual(overlap.map((r) => r.request_id));
        return recovered;
      }, { timeout: STEP.recovered });

      await test.step('with that user removed, the next sync is refused its key pair', async () => {
        sink.removeUser(second);
        expect(sink.users()).not.toContain(second.accessKeyId);
        await mintRequestLogs(actor, probe, `${label}-gone`, 1);
        await waitOutSyncCooldown(probe, connectionId);
        const pressed = await pressSync(page, connectionId, 'Sync Now', UI_READY);
        const failed = await syncAfter(probe, connectionId, pressed, 'failed');
        expect(Date.parse(failed.started)).toBeGreaterThan(Date.parse(delivered.started));
        expect(failed.error_message).toBe(keyPairRefused);
        const details = {
          kind: 'credentials_refused', type: 'ClientError', http_status: 403, vendor_code: 'InvalidAccessKeyId',
        };
        expect(failed.error_details).toEqual(details);
        const listed = (await syncLogsApi(actor, connectionId)).find((l) => l.id === failed.id);
        expect([listed?.status, listed?.error_message, listed?.error_details])
          .toEqual(['failed', keyPairRefused, details]);
        expect(await status()).toEqual({ status: 'error', status_message: keyPairRefused });
        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('alert').filter({ hasText: keyPairRefused })).toHaveCount(1, { timeout: UI_READY });
        await shot(page, testInfo, 'key-pair-refused');
      }, { timeout: STEP.removed });
    } finally {
      if (user) sink.removeUser(user);
    }
    await test.step('delete the connection', () =>
      deleteConnection(page, testInfo, actor, probe, connectionId, shown, UI_READY), { timeout: STEP.remove });
  },
);
