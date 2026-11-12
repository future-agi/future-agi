import { test, expect } from '../../lib/walk/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { StorageSink } from '../../lib/export-sinks';
import { WebhookSink } from '../../lib/sinks';
import { CONNECTIONS_PATH, VALIDATE_PATH, INTEGRATIONS_ROUTE, mintRequestLogs, firstSync,
  validateCredentials, editCredentials, storedCredentials, waitOutSyncCooldown, pressSync, syncAfter,
  deleteConnection, SYNC_NOW_COOLDOWN_MS, finishedSyncs } from '../../lib/integration-export';
import { UI_READY, REFUSAL_TEXT, fingerprint, safeJson, destinations, fillCredentials, connectDestination,
  inspectExportStack, assertNoCredentials, assertAppLogsClean, waitPastExportWindow, type Destination } from '../../lib/walk/export-destinations';
import { connectionRows, orgExportSnapshot, verified, assertDeliveryHistory, assertHistoryPage,
  pendingPreparation, recoverDestination } from '../../lib/walk/export-sync';

// GATEWAY-WALK-PLAN-706 W17; product ec94c6dba4. UI paths/labels, PG tables,
// API envelopes and sink shapes come from the cited INT-E2E base flow helpers.
// The two independently entered starts are both 18 h, excluding the 20 h row.
// The real product allows read/Sync Now to lower roles; management POST/PATCH/
// DELETE are the refused operations (CanManageIntegrations), not read-only sync.
const SYNC_BUDGET = SYNC_NOW_COOLDOWN_MS + UI_READY + POLL.ASYNC_JOB.timeout;
const STEP = {
  seed: 180_000,
  add: 4 * (3 * UI_READY + POLL.ASYNC_JOB.timeout),
  sync: 4 * (3 * SYNC_BUDGET + UI_READY),
  history: 5 * UI_READY,
  edit: 4 * (3 * UI_READY),
  recovery: 4 * (3 * SYNC_BUDGET + 2 * UI_READY),
  remove: 4 * UI_READY,
  roles: 6 * UI_READY,
  isolation: 3 * UI_READY,
};
const TIMEOUT = Object.values(STEP).reduce((a, b) => a + b, 0);
// synthetic test value — synthetic scope fixture configuration
test.use({ scopeRoster: { admin: true }, walkName: 'W17' });
test.describe.configure({ timeout: TIMEOUT });
test.skip(process.env.E2E_STACK === 'standalone', 'W17 export sinks are Distributed-only');

test('WALK-E2E-017: W17 an admin exports request logs, controls Sync Now and recovers failed destinations without crossing organizations', {
  tag: ['@flow', '@walk', '@walk-W17'],
  annotation: flowAnnotation({
    id: 'WALK-E2E-017', area: 'walks',
    userGoal: 'An admin connects Datadog, Mixpanel, PostHog and S3, controls exports and recovers failures while lower roles and another organization cannot change the connections',
    steps: [
      'Admin validates a refused credential then connects each declared local destination with an 18-hour start and 30-minute interval',
      'Sync Now, double-click while real preparation is pending, then navigate away with another preparation held before dispatch',
      'read run history and exported counts in the console, API and storage, including at-least-once repeats',
      'reject credential replacements while preserving stored credentials; inspect active controls as Member and Viewer',
      'revoke Datadog and Mixpanel credentials, stop PostHog and disable the isolated S3 user; read Error, Retry while down, restore each destination and Retry successfully',
      'delete each connection with confirmation, cancelling the first confirmation',
      'Member and Viewer have no usable management controls and their create, validate, edit and delete requests are refused',
      'org B cannot read A connections and its state remains byte-identical; error diagnostics and application logs hide credentials',
    ],
    backendChecks: [
      'connections are absent by default, refused validation creates nothing, and successful UI creates persist exactly the four destinations, active status and 1800-second intervals',
      'every successful sink delivery is exactly the org-A request IDs in its independently queried sync window, including documented repeats but never the 20-hour decoy or org B',
      'each double-click creates one dispatch and sync row; cancellation while preparation is pending creates neither a dispatch nor a row nor a delivery',
      'Sync History API and UI exactly match PG rows and counts; recovery rows read success, failed, failed, success, with sanitized Datadog credentials_refused details',
      'refused credential edits preserve ciphertext, and confirmed removal soft-deletes every connection and removes it from the API',
      'lower-role management writes are 403 without storage changes, cross-org reads are 404, B remains byte-identical, and no supplied credential appears in error details or application logs',
    ],
  }),
}, async ({ scopeActors: scopes, scopeProbe: probe, walk }, testInfo) => {
  test.setTimeout(TIMEOUT);
  expect(process.env.E2E_STACK ?? 'distributed', 'W17 requires locally built Distributed images').toBe('distributed');
  const inspection = inspectExportStack();
  expect(walk.inspection().error).toBeUndefined();
  walk.receipt.extra.exportStack = { storageImage: inspection.storageImage, configDigest: inspection.exportConfig,
    mountedSources: inspection.sourceHashes };
  const actor = scopes.owners[0]; // Use the fixture's owner and keys unchanged.
  const foreign = scopes.owners[1];
  const admin = scopes.admin!;
  // synthetic test value — per-run synthetic suffix
  const suffix = `${testInfo.project.name.slice(0, 2)}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const sink = new WebhookSink();
  const storage = new StorageSink();
  const ds: Destination[] = [];
  const context = await walk.open(admin, 'admin');
  const page = await context.newPage();
  const seeded = await test.step('seed synthetic request logs through the gateway webhook', async () => {
    expect(await connectionRows(probe, actor.organizationId)).toEqual([]);
    expect(await connectionRows(probe, foreign.organizationId)).toEqual([]);
    // synthetic test value — current synthetic request batch
    const current = await mintRequestLogs(actor, probe, `e2e-w17-${suffix}`);
    // synthetic test value — aged negative-window decoy
    const [old] = await mintRequestLogs(actor, probe, `e2e-w17-old-${suffix}`, 1, 20);
    // synthetic test value — foreign-organization synthetic request
    const other = await mintRequestLogs(foreign, probe, `e2e-w17-other-${suffix}`, 1);
    ds.push(...destinations(suffix, storage));
    const body = { scopeSet: fingerprint([actor.organizationId, foreign.organizationId]),
      current: current.map(l => fingerprint(l.request_id)), old: fingerprint(old.request_id),
      foreign: other.map(l => fingerprint(l.request_id)) };
    await testInfo.attach('seeded-export-identities', { contentType: 'application/json', body: safeJson(body) });
    walk.receipt.extra.scopeSet = body.scopeSet;
    return { ids: current.map(l => l.request_id), old: old.request_id, foreign: other.map(l => l.request_id) };
  }, { timeout: STEP.seed });
  const bBefore = await orgExportSnapshot(probe, foreign.organizationId);
  const secrets = ds.flatMap(d => [d.credential, d.refused, ...Object.values(d.fields), ...Object.values(d.badFields)]);
  try {
    await test.step('1 Admin adds each connection; bad validate creates nothing', async () => {
      await page.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
      await expect(page.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByTestId('integration-card-destination')).toHaveCount(0);
      for (const d of ds) {
        await fillCredentials(page, d, storage);
        const before = await connectionRows(probe, actor.organizationId);
        for (const [label, value] of Object.entries(d.badFields)) await page.getByLabel(label).fill(value, { timeout: UI_READY });
        expect(await validateCredentials(page, UI_READY)).toBe(400);
        await expect(page.getByRole('alert').filter({ hasText: REFUSAL_TEXT[d.kind] }))
          .toHaveText([REFUSAL_TEXT[d.kind]], { timeout: UI_READY });
        expect(await connectionRows(probe, actor.organizationId)).toEqual(before);
        d.id = await connectDestination(page, d);
        const first = await firstSync(probe, d.id);
        expect(first.status).toBe('success');
        expect(await probe.pg(`SELECT status, host_url, sync_interval_seconds, deleted
          FROM integrations_connection WHERE id = $1 AND organization_id = $2`, [d.id, actor.organizationId]))
          .toEqual([{ status: 'active', host_url: d.host, sync_interval_seconds: 1800, deleted: false }]);
        await assertDeliveryHistory(d, actor, probe, sink, storage, seeded.ids, seeded.old);
        await testInfo.attach(`connection-${d.kind}`, { contentType: 'application/json',
          body: safeJson({ identity: fingerprint(d.id), destination: d.kind }) });
      }
      verified(walk.receipt, 'default-off, validate and connection rows', 'storage', await connectionRows(probe, actor.organizationId));
    }, { timeout: STEP.add });

    await test.step('2 Sync Now, coalesced double-click, cancelled preparation', async () => {
      for (const d of ds) {
        // A 60-second cooldown moves the overlap past earlier inputs. Seed a
        // fresh request for each dispatch so success cannot mean an empty run.
        await waitPastExportWindow(probe, actor.organizationId);
        // synthetic test value — manual-sync synthetic input
        await mintRequestLogs(actor, probe, `e2e-w17-manual-${d.kind}-${suffix}`, 1);
        await page.goto(`${INTEGRATIONS_ROUTE}/${d.id}`, { waitUntil: 'domcontentloaded' });
        await waitOutSyncCooldown(probe, d.id!);
        const before = await finishedSyncs(probe, d.id!);
        const since = await pressSync(page, d.id!, 'Sync Now', UI_READY);
        await syncAfter(probe, d.id!, since, 'success');
        expect((await finishedSyncs(probe, d.id!)).length).toBe(before.length + 1);
        await waitPastExportWindow(probe, actor.organizationId);
        // synthetic test value — double-click synthetic input
        await mintRequestLogs(actor, probe, `e2e-w17-double-${d.kind}-${suffix}`, 1);
        verified(walk.receipt, `${d.kind} double-click`, 'api', await pendingPreparation(page, d, probe, sink, storage, 'double'));
        verified(walk.receipt, `${d.kind} cancellation`, 'api', await pendingPreparation(page, d, probe, sink, storage, 'cancel'));
        const delivery = await assertDeliveryHistory(d, actor, probe, sink, storage, seeded.ids, seeded.old);
        verified(walk.receipt, `${d.kind} exact deliveries including repeats`, 'storage', delivery);
      }
    }, { timeout: STEP.sync });

    await test.step('3 read exact history and counts as Admin and Owner', async () => {
      for (const d of ds) {
        verified(walk.receipt, `${d.kind} history parity`, 'ui', await assertHistoryPage(page, d, actor, probe));
        await walk.receipt.icons(page, 'admin', d.kind, walk.boundary.iconHostsAborted('admin'));
        await walk.receipt.shot(page, `${d.kind}-history`);
      }
      const ownerContext = await walk.open(scopes.ownerA, 'owner');
      const ownerPage = await ownerContext.newPage();
      await assertHistoryPage(ownerPage, ds[0], actor, probe);
      await ownerContext.close();
    }, { timeout: STEP.history });

    await test.step('4 refused credential edits preserve the stored ones', async () => {
      for (const d of ds) {
        await page.goto(`${INTEGRATIONS_ROUTE}/${d.id}`, { waitUntil: 'domcontentloaded' });
        const before = await storedCredentials(probe, d.id!);
        expect((await editCredentials(page, d.id!, d.platform, d.badFields, UI_READY)).status).toBe(400);
        expect(await storedCredentials(probe, d.id!)).toBe(before);
        verified(walk.receipt, `${d.kind} refused edit`, 'storage', before);
      }
      // Inspect active connections, not a vacuously empty page after deletion.
      for (const [role, member] of [['member', scopes.member], ['viewer', scopes.viewer]] as const) {
        const c = await walk.open(member, role); const p = await c.newPage();
        await p.goto(`${INTEGRATIONS_ROUTE}/${ds[0].id}`, { waitUntil: 'domcontentloaded' });
        if (role === 'viewer') {
          // ec94c6dba4 RoleProtection renders NoAccessState at the requested
          // URL (not the older redirect behavior); no management control.
          await expect(p.getByTestId('settings-no-access')).toBeVisible({ timeout: UI_READY });
          await expect(p.getByRole('heading', { name: "You don't have access to this page" })).toBeVisible({ timeout: UI_READY });
          await expect(p.getByRole('button', { name: 'Integration options menu' })).toHaveCount(0);
          await expect(p.getByRole('button', { name: 'Pause', exact: true })).toHaveCount(0);
        } else {
          await expect(p.getByRole('button', { name: 'Integration options menu' })).toBeDisabled({ timeout: UI_READY });
          await expect(p.getByRole('button', { name: 'Pause', exact: true })).toBeDisabled({ timeout: UI_READY });
        }
        await expect(p.getByRole('dialog')).toHaveCount(0);
        await walk.receipt.shot(p, `${role}-readonly`); await c.close();
      }
      for (const d of ds) expect((await scopes.send(scopes.ownerB, 'GET', `${CONNECTIONS_PATH}${d.id}/`)).status).toBe(404);
    }, { timeout: STEP.edit });

    await test.step('5 destination failures, Retry down, Retry restored', async () => {
      for (const d of ds) {
        // Mint a fresh input before the outage, so a retry cannot pass on no work.
        await waitPastExportWindow(probe, actor.organizationId);
        // synthetic test value — recovery synthetic input
        await mintRequestLogs(actor, probe, `e2e-w17-late-${d.kind}-${suffix}`, 1);
        const rows = await recoverDestination(page, d, probe, sink);
        assertNoCredentials(rows, secrets);
        verified(walk.receipt, `${d.kind} failure and recovery`, 'storage', rows);
        await assertHistoryPage(page, d, actor, probe);
        await assertDeliveryHistory(d, actor, probe, sink, storage, seeded.ids, seeded.old);
        await walk.receipt.shot(page, `${d.kind}-recovered`);
      }
    }, { timeout: STEP.recovery });

    await test.step('6 delete with confirm and cancelled confirmation', async () => {
      for (const d of ds) {
        await deleteConnection(page, testInfo, actor, probe, d.id!, { name: d.name, destination: d.destination }, UI_READY);
        verified(walk.receipt, `${d.kind} soft delete`, 'storage',
          await probe.pg('SELECT deleted FROM integrations_connection WHERE id = $1', [d.id]));
      }
      const listed = await actor.api.get<{ result: { connections: unknown[] } }>(CONNECTIONS_PATH);
      expect(listed.result.connections).toEqual([]);
    }, { timeout: STEP.remove });

    await test.step('7 Member and Viewer cannot add, edit or remove', async () => {
      const before = await orgExportSnapshot(probe, actor.organizationId);
      for (const [role, member] of [['member', scopes.member], ['viewer', scopes.viewer]] as const) {
        const c = await walk.open(member, `${role}-catalog`); const p = await c.newPage();
        await p.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
        const addButtons = p.getByRole('button', { name: 'Add Integration', exact: true });
        if (role === 'viewer') {
          await expect(p.getByTestId('settings-no-access')).toBeVisible({ timeout: UI_READY });
          await expect(p.getByRole('heading', { name: "You don't have access to this page" })).toBeVisible({ timeout: UI_READY });
          await expect(addButtons).toHaveCount(0);
        } else {
          await expect(p.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(addButtons).toHaveCount(2, { timeout: UI_READY });
          for (const button of await addButtons.all()) await expect(button).toBeDisabled({ timeout: UI_READY });
        }
        const requests = [
          ['POST', CONNECTIONS_PATH], ['POST', VALIDATE_PATH],
          ['PATCH', `${CONNECTIONS_PATH}${ds[0].id}/`], ['DELETE', `${CONNECTIONS_PATH}${ds[0].id}/`],
        ];
        for (const [method, path] of requests) expect((await scopes.send(member, method, path, {})).status).toBe(403);
        await c.close();
      }
      expect(await orgExportSnapshot(probe, actor.organizationId)).toEqual(before);
      verified(walk.receipt, 'role denials preserve all rows', 'storage', before);
    }, { timeout: STEP.roles });

    await test.step('8 org B is byte-identical and diagnostics contain no credentials', async () => {
      expect(await orgExportSnapshot(probe, foreign.organizationId)).toEqual(bBefore);
      const b = await scopes.ownerB.api.get<{ result: { connections: unknown[] } }>(CONNECTIONS_PATH);
      expect(b.result.connections).toEqual([]);
      for (const d of ds) {
        const rows = await finishedSyncs(probe, d.id!); assertNoCredentials(rows, secrets);
        const result = await assertDeliveryHistory(d, actor, probe, sink, storage, seeded.ids, seeded.old);
        expect(result.deliveryIds.filter(id => seeded.foreign.includes(id))).toEqual([]);
      }
      verified(walk.receipt, 'org B and credential-free diagnostics', 'storage',
        { snapshot: fingerprint(bBefore), logs: assertAppLogsClean(secrets) });
      // synthetic test value — synthetic narrow viewport
      await page.setViewportSize({ width: 390, height: 844 });
      // synthetic test value — synthetic dark-mode condition
      await page.emulateMedia({ colorScheme: 'dark' });
      await walk.receipt.shot(page, 'removed-dark-narrow');
    }, { timeout: STEP.isolation });
  } catch (error) {
    walk.receipt.verdict({ feature: 'W17 assertion failure', lane: 'storage', where: 'export lifecycle',
      verdict: 'RECORDED', detail: { result: 'fail',
        queriedDigest: fingerprint(error instanceof Error ? error.message : String(error)) } });
    throw error;
  } finally {
    for (const d of ds) if (d.user) storage.removeUser(d.user);
    await context.close();
  }
});
