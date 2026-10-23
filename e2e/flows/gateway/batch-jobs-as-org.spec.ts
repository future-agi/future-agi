import type { Page, Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { WebhookSink } from '../../lib/sinks';
import { StackLock, STACK_LOCKS } from '../../lib/stack-lock';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the console (frontend/src/sections/gateway/settings/GatewaySettingsSection.jsx):
//   Batch Jobs at TAB_SLUGS "batch-jobs" (:944); "Submit Batch Job" wrapped in
//   GatewayAdminOnly (components/GatewayAdminOnly.jsx: a Tooltip on a span, title
//   constants/gatewayAdmin.js GATEWAY_ADMIN_ONLY "Owner or Admin only") and disabled
//   for a non-admin, whose empty state reads "No batch jobs submitted yet. An Owner
//   or Admin can submit one."; the dialog "Submit Batch Job" (one multiline JSON
//   textbox, a slider, "Submit"); a tracked job row (batch id's first 12 characters
//   + "...", the status chip polled from get-batch every 3 s, settings/hooks/
//   useBatchJobs.js); "View Details" -> dialog "Batch Details" with StatBoxes Total /
//   Completed / Failed and a Response Preview column (the item's first choice);
//   the header's "Health Check" (snackbar "Health check complete") and Gateway Info's
//   "Last Health Check" ("<toLocaleString> (<status>)").
// - the wire (useBatchJobs.js, utils/axios.js endpoints.gateway.batch): POST
//   /agentcc/gateways/default/submit-batch/ { requests, max_concurrency: 5 },
//   GET .../get-batch/?batch_id=, both answered { status, result } by Django
//   (futureagi/agentcc/views/gateway.py submit_batch / get_batch); submit_batch is in
//   gateway_admin_actions (:462), so a non-admin gets 403 with
//   agentcc/permissions.py GATEWAY_ADMIN_MESSAGE; get_batch answers 404 "Batch not
//   found" to another org (batch_jobs.job_for). health_check re-pushes every org's
//   config only for an admin (is_gateway_admin) and answers org_configs_pushed.
// - the push stamp: a push sends meta.pushed_at (agentcc/services/config_push.py
//   _config_meta), the gateway's 60 s bulk sync replaces each org config without it
//   (agentcc-gateway/internal/tenant/store.go MergeBulk), and the admin read keeps
//   meta (internal/server/admin_orgs.go redactOrgConfig); the provider create
//   pushes the org's first config (views/provider_credential.py). Any admin's
//   Health Check, in any org, re-pushes every org's config (views/gateway.py
//   _repush_org_configs -> push_all_org_configs), so #3's window, from the
//   unstamped precondition to the stamp read, holds the stack lock that every
//   flow clicking Health Check as an admin takes around its click
//   (lib/stack-lock.ts; GW-E2E-010): under the suite's parallel workers no other
//   flow's re-push can stamp this org inside it.
// - the batch as the org (agentcc/services/batch_jobs.py): a console-batch-<uuid>
//   key minted for the org, agentcc_batch_job (organization_id, created_by_id,
//   gateway_batch_id, gateway_key_id, status, total, key_revoked_at); at the first
//   terminal status seen, one batch.completed event per subscribed webhook
//   (payload event, batch_id, org_id, status, total, created_at, completed_at,
//   summary) delivered after commit, and the key revoked here and at the gateway
//   (internal/auth/keystore.go status "revoked", GET /-/keys/{id}; 404 once the key
//   sync drops it). The gateway runs each item as POST /v1/chat/completions with
//   the minted key (internal/batch/run_as_request.go); each result names its
//   request_id (internal/batch/batch.go Result); GET and cancel /-/batches/{id}
//   answer 404 for any other org (internal/server/server.go getBatchHandler,
//   cancelBatchHandler).
// - the request log (agentcc/models/request_log.py, agentcc_request_log:
//   organization_id, request_id, model, provider, api_key_id = the gateway key id,
//   status_code; agentcc/services/log_ingestion.py); the org provider is the one an
//   org-first route names (internal/server/handlers.go resolveProviderOrgFirst).
// - wire bodies: AgentccProviderCredentialCreateSerializer (GW-E2E-002, harness/mock-upstream.spec.ts), POST /agentcc/api-keys/ { name } (GW-E2E-007), AgentccWebhookSerializer
//   { name, url, secret, events, is_active } (harness/webhook-sink.spec.ts).
// The batch's trace id is checked by
// GW-E2E-018 on the alt profile, the only one whose request log keeps request
// headers; no request-log column holds a trace id.
const BATCH_JOBS_PATH = '/dashboard/gateway/settings/batch-jobs';
const SETTINGS_PATH = '/dashboard/gateway/settings';
const SUBMIT_BATCH = '/agentcc/gateways/default/submit-batch/';
const GET_BATCH = '/agentcc/gateways/default/get-batch/';
const SUBMIT_BATCH_PATH = /\/agentcc\/gateways\/[^/]+\/submit-batch\/$/;
const HEALTH_CHECK = /\/agentcc\/gateways\/[^/]+\/health_check\/$/;
const GATEWAY_ADMIN_ONLY = 'Owner or Admin only';
const MEMBER_EMPTY = 'No batch jobs submitted yet. An Owner or Admin can submit one.';
const GATEWAY_ADMIN_MESSAGE = 'Only an organization Owner or Admin can change the gateway\'s configuration.';
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const BATCH_DONE = POLL.ASYNC_JOB; // a console batch's items finishing on the echo mock
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
const WEBHOOK_DELIVERED = POLL.ASYNC_JOB; // a webhook event delivered after commit
// Waits on another worker's admin Health Check click and its response (GW-E2E-010
// holds the lock from just before its click until the response).
const REPUSH_LOCK_WAIT = UI_READY;
// provisioning 120 + seeding 15 + Member Batch Jobs step 75 + #1/#2 5 + GATEWAY_SYNC
// precondition 75 + Member Health Check step 75 + #3 2 + Admin submit step 75 + first
// BATCH_DONE 60 + #4 BATCH_DONE 60 + #5 LOG_VISIBLE 60 + Details step 75 + #7 5 + #8
// WEBHOOK_DELIVERED 60 + #9 ASYNC_JOB 60 + headroom 30 = 852 s, + REPUSH_LOCK_WAIT 60
// = 912 s, rounded up.
const FLOW_TIMEOUT = 960_000;

type Created<T> = { result: T };
type BatchResult = { batch_id: string; status: string; total: number;
  results?: { index: number; status: string; request_id?: string }[] };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-009: a console batch job runs as the organization: served by its own provider, logged and announced as its, refused to a member, hidden from another organization',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-009',
      area: 'gateway',
      userGoal:
        'An org admin runs a console batch job that is served by the organization\'s own provider, logged and announced as the organization\'s, while a member can only watch and check health, and another organization cannot see or cancel it. The batch\'s trace id is checked by GW-E2E-018 on the alt profile, the only one whose request log keeps request headers',
      steps: [
        'seed the org\'s own provider on the mock, an org key, and a webhook subscribed to batch.completed',
        'as a member, open Gateway Settings, then Batch Jobs, and see Submit Batch Job disabled, Owner or Admin only',
        'as the member, wait until the gateway\'s copy of the org config carries no push stamp, then click Health Check on Settings',
        'as the admin, open Batch Jobs, click Submit Batch Job, replace the JSON with two requests for the org\'s model, submit, and watch the job reach completed',
        'open View Details',
        'leave Batch Jobs for Settings',
        'as another organization\'s owner, try to read and cancel the batch; read it twice more as the admin',
      ],
      backendChecks: [
        'a Member\'s submit-batch POST is refused with 403',
        'the Member\'s refusal leaves no agentcc_batch_job row and no console-batch key for the org',
        'the Member\'s health check pushes no org config to the gateway: it answers org_configs_pushed false and the gateway\'s copy of the org config gains no push stamp',
        'the Admin\'s batch is recorded under the org with both items and reaches completed',
        'each item is one request log row under the org, under the batch\'s minted key, with the request id the batch result names, served by the org provider',
        'the org provider was called with the org\'s own credential for each item',
        'another org cannot read or cancel the batch: the gateway answers 404 for that org\'s id on get and cancel, and Django answers 404 to that org',
        'the org\'s webhook receives batch.completed exactly once, for this batch, as the org, even after the batch is read again',
        'the batch\'s minted key is revoked in Django and at the gateway once the batch finished',
      ],
      browserChecks: [
        'the Member sees Submit Batch Job disabled with "Owner or Admin only" and the Member empty state',
        'the Member\'s Health Check shows "Health check complete" and Last Health Check ends "(healthy)"',
        'the Admin\'s job row shows completed',
        'Batch Details shows Total 2, Completed 2, Failed 0 and both items\' echoes',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const member = scopes.member;
    const other = scopes.ownerB;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: only this flow's items use the model (#5); the node's own
    // provider key is "e2e-mock", so the minted key at the mock proves the org's
    // provider served each item (#6); prompts anchor #6 and the Details echoes;
    // scenario and channel are this flow's alone (#6, #8).
    const scenario = `gw9-${suffix}`;
    const channel = `gw9-${suffix}`;
    const providerName = `e2e-gw9-${suffix}`;
    const providerKey = `sk-e2e-gw9-${suffix}`;
    const model = `e2e-gw9-model-${suffix}`;
    const itemA = { model, messages: [{ role: 'user', content: `e2e-gw9-${suffix}-a` }] };
    const itemB = { model, messages: [{ role: 'user', content: `e2e-gw9-${suffix}-b` }] };

    const upstream = new MockUpstream();
    const sink = new WebhookSink();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: providerKey }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [model], extra_config: { api_path_prefix: '/v1' },
    });
    const orgKey = await owner.api.post<Created<{ gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw9-${suffix}` });
    const webhook = await owner.api.post<Created<{ id: string }>>('/agentcc/webhooks/', {
      name: `e2e-gw9-${suffix}`, url: sink.url(channel), secret: `e2e-gw9-secret-${suffix}`,
      events: ['batch.completed'], is_active: true,
    });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, channel, model, organizationId: owner.organizationId,
        otherOrganizationId: other.organizationId, memberUserId: member.userId, providerId: provider.result.id,
        orgKeyGatewayId: orgKey.result.gateway_key_id, webhookId: webhook.result.id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const pushStamp = async () => {
      const cfg = await admin.orgConfig(owner.organizationId);
      return cfg === null ? 'absent' : (cfg.meta && 'pushed_at' in cfg.meta ? 'stamped' : 'unstamped');
    };
    // For the report only (no assertion): the gateway's copy right after the provider
    // create's push, so a reader can see the stamp #3's precondition waits to clear.
    await testInfo.attach('stamp-after-seed', {
      contentType: 'application/json',
      body: JSON.stringify({ at: new Date().toISOString(), meta: (await admin.orgConfig(owner.organizationId))?.meta ?? null }),
    });
    const memberContext = await scopes.openContext(browser, member);
    const ownerContext = await scopes.openContext(browser, owner);
    const lastHealthCheck = (p: Page) => p.locator('.MuiCard-root')
      .filter({ has: p.getByRole('heading', { name: 'Gateway Info', exact: true }) })
      .getByText('Last Health Check', { exact: true }).locator('xpath=following-sibling::p[1]');
    let batchId = '';
    let keyId = '';
    let repush: StackLock | undefined;
    try {
      const memberPage = await memberContext.newPage();
      await test.step('UI: a member sees Submit Batch Job disabled, Owner or Admin only', async () => {
        await memberPage.goto(BATCH_JOBS_PATH, { waitUntil: 'domcontentloaded' });
        const submit = memberPage.getByRole('button', { name: 'Submit Batch Job', exact: true });
        await expect(submit).toBeDisabled({ timeout: UI_READY });
        await expect(memberPage.getByLabel(GATEWAY_ADMIN_ONLY, { exact: true }).filter({ has: submit })).toHaveCount(1);
        await expect(memberPage.getByText(MEMBER_EMPTY, { exact: true })).toBeVisible({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('API + storage: the member\'s submit is refused and leaves nothing behind', async () => {
        const refused = await scopes.send(member, 'POST', SUBMIT_BATCH, { requests: [itemA], max_concurrency: 5 });
        expect({ status: refused.status, says: JSON.stringify(refused.body).includes(GATEWAY_ADMIN_MESSAGE) })
          .toEqual({ status: 403, says: true });
        const left = await probe.pg<{ jobs: number; keys: number }>(
          `SELECT (SELECT count(*) FROM agentcc_batch_job WHERE organization_id = $1)::int AS jobs,
                  (SELECT count(*) FROM agentcc_api_key WHERE organization_id = $1 AND name LIKE 'console-batch-%')::int AS keys`,
          [owner.organizationId]);
        expect(left).toEqual([{ jobs: 0, keys: 0 }]);
      });

      // The stack lock: no other flow's admin Health Check may re-push every org's config inside #3's window.
      repush = await StackLock.acquire(STACK_LOCKS.ORG_CONFIG_REPUSH, { timeoutMs: REPUSH_LOCK_WAIT });
      await testInfo.attach('repush-lock', { contentType: 'application/json', body: JSON.stringify({ waitedMs: repush.waitedMs }) });
      await test.step('UI + gateway: the member\'s Health Check works and re-pushes nothing', async () => {
        // Precondition: the provider create's push stamp has been cleared by a sync, so a
        // stamp after the click could only come from a re-push by it.
        await expect.poll(pushStamp, POLL.GATEWAY_SYNC).toBe('unstamped');
        await memberPage.goto(SETTINGS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(lastHealthCheck(memberPage)).toBeVisible({ timeout: UI_READY });
        const checked = memberPage.waitForResponse(
          (r) => r.request().method() === 'POST' && HEALTH_CHECK.test(new URL(r.url()).pathname), { timeout: UI_READY });
        await memberPage.getByRole('button', { name: 'Health Check', exact: true }).click();
        const response = await checked;
        expect(response.status()).toBe(200);
        await expect(memberPage.getByText('Health check complete', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(lastHealthCheck(memberPage)).toHaveText(/ \(healthy\)$/, { timeout: UI_READY });
        const pushed = ((await response.json()) as { result: { org_configs_pushed: unknown } }).result.org_configs_pushed;
        expect({ pushed, stamp: await pushStamp() }).toEqual({ pushed: false, stamp: 'unstamped' });
      }, { timeout: STEP_BUDGET + POLL.GATEWAY_SYNC.timeout });
      await repush.release();

      const page = await ownerContext.newPage();
      const row = () => page.locator('tbody tr').filter({ hasText: `${batchId.slice(0, 12)}...` });
      await test.step('UI: the admin submits a two-request batch and watches it complete', async () => {
        await page.goto(BATCH_JOBS_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Submit Batch Job', exact: true }).click({ timeout: UI_READY });
        const dialog = page.getByRole('dialog', { name: 'Submit Batch Job' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        await dialog.getByRole('textbox').fill(JSON.stringify([itemA, itemB], null, 2));
        const submitted = page.waitForResponse(
          (r: Response) => r.request().method() === 'POST' && SUBMIT_BATCH_PATH.test(new URL(r.url()).pathname),
          { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Submit', exact: true }).click();
        const response = await submitted;
        expect(response.status()).toBe(200);
        expect(response.request().postDataJSON()).toEqual({ requests: [itemA, itemB], max_concurrency: 5 });
        batchId = ((await response.json()) as { result: BatchResult }).result.batch_id;
        expect(batchId).toMatch(/\S{12,}/);
        await testInfo.attach('batch', { contentType: 'application/json', body: JSON.stringify({ batchId }) });
        await expect(page.locator('tbody tr td:first-child')).toHaveText([`${batchId.slice(0, 12)}...`], { timeout: UI_READY });
        await expect(row().locator('.MuiChip-label')).toHaveText(['completed'], { timeout: BATCH_DONE.timeout });
      }, { timeout: STEP_BUDGET + BATCH_DONE.timeout });

      await test.step('storage: the batch is the org\'s, with both items, completed', async () => {
        await expect.poll(async () => probe.pg<{ org: string; by: string; total: number; status: string }>(
          `SELECT organization_id::text AS org, created_by_id::text AS by, total, status
             FROM agentcc_batch_job WHERE gateway_batch_id = $1`, [batchId]), BATCH_DONE)
          .toEqual([{ org: owner.organizationId, by: owner.userId, total: 2, status: 'completed' }]);
      });

      await test.step('storage: each item is one request log row as the org, under the minted key, served by the org provider', async () => {
        keyId = (await probe.pg<{ key: string }>(
          'SELECT gateway_key_id AS key FROM agentcc_batch_job WHERE gateway_batch_id = $1', [batchId]))[0].key;
        const batch = (await owner.api.get<{ result: BatchResult }>(GET_BATCH, { batch_id: batchId })).result;
        const requestIds = (batch.results ?? []).map((r) => r.request_id ?? '').sort();
        await testInfo.attach('rows', { contentType: 'application/json', body: JSON.stringify({ keyId, requestIds }) });
        await expect.poll(async () => probe.pg<{ request_id: string }>(
          `SELECT request_id, api_key_id, provider, status_code FROM agentcc_request_log
            WHERE organization_id = $1 AND model = $2 ORDER BY request_id`, [owner.organizationId, model]), LOG_VISIBLE)
          .toEqual(requestIds.map((request_id) => ({ request_id, api_key_id: keyId, provider: providerName, status_code: 200 })));
      });

      await test.step('UI: Batch Details shows both items done, each the mock\'s echo', async () => {
        await row().getByRole('button', { name: 'View Details', exact: true }).click();
        const details = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: 'Batch Details', exact: true }) });
        await expect(details).toBeVisible({ timeout: UI_READY });
        for (const [label, value] of [['Total', '2'], ['Completed', '2'], ['Failed', '0']]) {
          await expect(details.getByText(label, { exact: true }).locator('xpath=following-sibling::*[1]')).toHaveText(value, { timeout: UI_READY });
        }
        await expect(details.locator('tbody tr td:nth-child(6)'))
          .toHaveText([`echo: e2e-gw9-${suffix}-a`, `echo: e2e-gw9-${suffix}-b`], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('upstream: the org provider was called with the org\'s own credential for each item', async () => {
        const seen = (await upstream.requests(scenario)).map((r) => [r.headers.authorization,
          (r.body as { messages: { content: string }[] }).messages.at(-1)?.content]).sort();
        expect(seen).toEqual([[`Bearer ${providerKey}`, `e2e-gw9-${suffix}-a`], [`Bearer ${providerKey}`, `e2e-gw9-${suffix}-b`]]);
      });

      await test.step('UI: leave Batch Jobs (no console get-batch read from here)', async () => {
        await page.goto(SETTINGS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(lastHealthCheck(page)).toBeVisible({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('gateway + API: another organization cannot read or cancel the batch', async () => {
        const gwGet = (await admin.batch(batchId, other.organizationId)).status;
        const gwCancel = (await admin.cancelBatch(batchId, other.organizationId)).status;
        const django = (await scopes.send(other, 'GET', GET_BATCH, undefined, { batch_id: batchId })).status;
        const own = (await admin.batch(batchId, owner.organizationId)).status;
        expect({ gwGet, gwCancel, django, own }).toEqual({ gwGet: 404, gwCancel: 404, django: 404, own: 200 });
      });

      await test.step('sink: batch.completed arrives exactly once, for this batch, as the org', async () => {
        for (let i = 0; i < 2; i++) await owner.api.get(GET_BATCH, { batch_id: batchId });
        await expect.poll(async () => (await sink.received(channel)).map((r) => {
          const body = r.body as { event?: string; batch_id?: string; org_id?: string; status?: string };
          return { event: body.event, batch_id: body.batch_id, org_id: body.org_id, status: body.status };
        }), WEBHOOK_DELIVERED).toEqual([{ event: 'batch.completed', batch_id: batchId, org_id: owner.organizationId, status: 'completed' }]);
      });

      await test.step('storage + gateway: the minted key is revoked once the batch finished', async () => {
        await expect.poll(async () => {
          const rows = await probe.pg<{ status: string; recorded: boolean }>(
            `SELECT k.status, j.key_revoked_at IS NOT NULL AS recorded
               FROM agentcc_batch_job j JOIN agentcc_api_key k
                 ON k.gateway_key_id = j.gateway_key_id AND k.organization_id = j.organization_id
              WHERE j.gateway_batch_id = $1`, [batchId]);
          const gw = await admin.key(keyId);
          return { django: rows[0]?.status ?? null, recorded: rows[0]?.recorded ?? null,
            gateway: gw.status === 404 ? 'gone' : (gw.body as { status?: string })?.status ?? `http ${gw.status}` };
        }, POLL.ASYNC_JOB).toEqual({ django: 'revoked', recorded: true, gateway: expect.stringMatching(/^(revoked|gone)$/) });
      });
    } finally {
      await admin.dispose();
      await memberContext.close();
      await ownerContext.close();
      await repush?.release();
    }
  },
);
