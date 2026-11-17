import { createHash, createHmac, randomBytes } from 'node:crypto';
import type { Page, Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin, GATEWAY_ADMIN_TOKEN } from '../../lib/gateway-admin';
import { E2E } from '../../lib/env';
import { WebhookSink } from '../../lib/sinks';
import { StackLock, STACK_LOCKS } from '../../lib/stack-lock';
import { POLL } from '../../lib/state-probe';
import { MockUpstream, syntheticProviderKey } from '../../lib/upstream';

// Pinned off the source at e1ef026f78 (REL-R7 9.5 row E2E-R7-1; lanes BATCH and I1):
// - the console (frontend/src/sections/gateway/settings/GatewaySettingsSection.jsx):
//   Batch Jobs at TAB_SLUGS "batch-jobs" (:944); "Submit Batch Job" wrapped in
//   GatewayAdminOnly (components/GatewayAdminOnly.jsx: a Tooltip on a span, title
//   constants/gatewayAdmin.js GATEWAY_ADMIN_ONLY "Owner or Admin only") and disabled
//   for a non-admin, whose empty state reads "No batch jobs submitted in this view. An
//   Owner or Admin can submit one." (settings/batch/BatchJobsTab.jsx:399, with delegated
//   batch submission on; with it off, every role reads :396 "No batch jobs submitted in
//   this view. Batch submission is not available in this release; you can still open a
//   job by its ID." and Submit Batch Job is disabled for the admin too, :283-:284, :293-:297);
//   the dialog "Submit Batch Job" (one multiline JSON
//   textbox, a slider, "Submit"); a tracked job row (the full batch id in the first cell with a
//   Copy button, BatchJobsTab.jsx:574-586, the status chip polled from get-batch every 3 s, settings/hooks/
//   useBatchJobs.js); "View Details" -> dialog "Batch Details" with StatBoxes Total /
//   Completed / Failed and a Response column (the item's first choice);
//   the header's "Health Check", on every Settings tab, wrapped in GatewayAdminOnly
//   and disabled for a non-admin as Submit Batch Job is (useGatewayAdmin canEdit,
//   the source Providers, API keys and Budgets use), and Gateway Info's
//   "Last Health Check" ("<toLocaleString> (<status>)").
// - the wire (useBatchJobs.js, utils/axios.js endpoints.gateway.batch): POST
//   /agentcc/gateways/default/submit-batch/ { requests, max_concurrency: 5 },
//   GET .../get-batch/?batch_id=, both answered { status, result } by Django
//   (futureagi/agentcc/views/gateway.py submit_batch / get_batch); submit_batch is in
//   gateway_admin_actions (:462), so a non-admin gets 403 with
//   agentcc/permissions.py GATEWAY_ADMIN_MESSAGE; get_batch answers 404 "Batch not
//   found" to another org (batch_jobs.job_for). health_check re-pushes every org's
//   config only for an admin (is_gateway_admin) and answers org_configs_pushed; it
//   is not in gateway_admin_actions, so a member's own request, POST
//   /agentcc/gateways/default/health_check/ (utils/axios.js endpoints.gateway
//   .healthCheck), is answered, and records the org's check as an admin's does
//   (_record_health_check).
// - the push stamp: a push sends meta.pushed_at (agentcc/services/config_push.py
//   _config_meta), the gateway's 60 s bulk sync replaces each org config without it
//   (agentcc-gateway/internal/tenant/store.go MergeBulk), and the admin read keeps
//   meta (internal/server/admin_orgs.go redactOrgConfig); the provider create
//   pushes the org's first config (views/provider_credential.py). Any admin's
//   Health Check, in any org, re-pushes every org's config (views/gateway.py
//   _repush_org_configs -> push_all_org_configs), so #3's window, from the
//   unstamped precondition to the stamp read, holds the stack lock that every
//   flow clicking Health Check as an admin takes around its click (H9,
//   lib/stack-lock.ts; GW-E2E-010): under the suite's parallel workers no other
//   flow's re-push can stamp this org inside it.
// - the batch as the org (updated at 7f5246c90f83): delegated_batches.py:28-82
//   issues a registered batch ServiceGrant, not an AgentccAPIKey; its principal
//   is dlg_<jti>. agentcc_batch_job (organization_id, created_by_id,
//   gateway_batch_id, gateway_key_id, status, total, key_revoked_at); at the first
//   terminal status seen, one batch.completed event per subscribed webhook
//   (payload event, batch_id, org_id, status, total, created_at, completed_at,
//   summary) delivered after commit. delegated_batches.py:135-143 revokes the
//   ServiceGrant and stamps key_revoked_at; /-/keys/{dlg_id} is NOT a revocation
//   proof. The gateway runs each item as POST /v1/chat/completions with
//   the delegated principal (internal/batch/run_as_request.go); each result names its
//   request_id (internal/batch/batch.go Result); GET and cancel /-/batches/{id}
//   answer 404 for any other org (internal/server/server.go getBatchHandler,
//   cancelBatchHandler).
// - the request log (agentcc/models/request_log.py, agentcc_request_log:
//   organization_id, request_id, model, provider, api_key_id = the gateway key id,
//   status_code; agentcc/services/log_ingestion.py); the org provider is the one an
//   org-first route names (internal/server/handlers.go resolveProviderOrgFirst).
// - wire bodies: AgentccProviderCredentialCreateSerializer (GW-E2E-002, the H2 live
//   spec), POST /agentcc/api-keys/ { name } (GW-E2E-007), AgentccWebhookSerializer
//   { name, url, secret, events, is_active } (the H1 live spec).
// Narrowing (REL-R7 E2E plan, Q1): the row's "trace id" clause is carried by
// GW-E2E-018 on the r7alt profile, the only one whose request log keeps request
// headers; no request-log column holds a trace id.
// Settings tabs live in ?tab= (settings/GatewaySettingsSection.jsx tabPath; an old
// /settings/<tab> link is moved there).
const BATCH_JOBS_PATH = '/dashboard/gateway/settings?tab=batch-jobs';
const SETTINGS_PATH = '/dashboard/gateway/settings';
const SUBMIT_BATCH = '/agentcc/gateways/default/submit-batch/';
const GET_BATCH = '/agentcc/gateways/default/get-batch/';
const SUBMIT_BATCH_PATH = /\/agentcc\/gateways\/[^/]+\/submit-batch\/$/;
const HEALTH_CHECK = '/agentcc/gateways/default/health_check/';
const GATEWAY_ADMIN_ONLY = 'Owner or Admin only';
const MEMBER_EMPTY = 'No batch jobs submitted in this view. An Owner or Admin can submit one.';
// BatchJobsTab.jsx:74, shown in an Alert (:293-:297) and in the empty state (:396) when the
// deployment has delegated batch submission off (AGENTCC_DELEGATED_BATCH_ENABLED, default
// false: agentcc/services/delegated_availability.py delegated_enabled; submit-batch then
// answers 409 feature_unavailable_733).
const BATCH_SUBMIT_OFF = 'Batch submission is not available in this release.';
const GATEWAY_ADMIN_MESSAGE = 'Only an organization Owner or Admin can change the gateway\'s configuration.';
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on (section 0 of the REL-R7 E2E plans).
const BATCH_DONE = POLL.ASYNC_JOB; // a console batch's items finishing on the echo mock
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
const WEBHOOK_DELIVERED = POLL.ASYNC_JOB; // a webhook event delivered after commit
// Waits on another worker's admin Health Check click and its response (H9; GW-E2E-010
// holds the lock from just before its click until the response).
const REPUSH_LOCK_WAIT = UI_READY;
// provisioning 120 + seeding 15 + Member Batch Jobs step 75 + #1/#2 5 + GATEWAY_SYNC
// precondition 75 + Member Health Check step 75 + #3 2 + Admin submit step 75 + B-c1
// BATCH_DONE 60 + #4 BATCH_DONE 60 + #5 LOG_VISIBLE 60 + Details step 75 + #7 5 + #8
// WEBHOOK_DELIVERED 60 + #9 ASYNC_JOB 60 + headroom 30 = 852 s, + REPUSH_LOCK_WAIT 60
// = 912 s, rounded up.
const FLOW_TIMEOUT = 960_000;

type Created<T> = { result: T };
type BatchResult = { batch_id: string; status: string; total: number;
  results?: { index: number; status: string; request_id?: string }[] };

// Source contract at 7f5246c90f83:
// auth/envelope.go:63-65,157-175,390-451; server/delegated.go:149-211;
// auth/delegated_eligibility.go:46-56,99-128; tenant/spend_sync.go:runPeriodicSync.
// A healthy next fetch starts within 60s and has a 15s HTTP deadline. Use the
// existing GATEWAY_SYNC budget, anchored to revocation, not a fresh wait window.
// The 2-interval stale-snapshot allowance is outage tolerance, not this deadline.
const GRANT_REVOCATION_WINDOW_MS = POLL.GATEWAY_SYNC.timeout;
type BatchGrant = { jti: string; kid: string; aud: string; kind: string; org: string;
  workspace: string; user: string; via: string; sub: string; scope: string; iat: string; exp: string };

/** Local-only verifier fixture, NOT a reconstruction of FA's signed grant.
 * The registry stores claims, not bearer bytes. The gateway authenticates the
 * envelope and treats its digest-bound grant MAC as opaque (envelope.go:444-451).
 * Re-encode the exact registered claims with a dummy fixture MAC; never forward
 * this token to FA. The positive control changes ONLY jti, not membership,
 * lifetime, route, body or signing. It is not registered and never submits work.
 * Native fetch keeps the two wire tokens out of Playwright request traces.
 */
function delegatedListProbe(grant: BatchGrant, batchId: string) {
  const url = new URL('/-/batches', E2E.gatewayUrl);
  if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      || !['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || GATEWAY_ADMIN_TOKEN !== 'local-dev-only-admin-token-replace-me') {
    throw new Error('GW009 delegated proof requires the loopback public dummy admin fixture');
  }
  const digest = (value: string) => createHash('sha256').update(value).digest('hex');
  const wire = (fields: string[]) => {
    const canonical = fields.join('\n');
    const prk = createHmac('sha256', 'fe1').update(GATEWAY_ADMIN_TOKEN).digest();
    const key = createHmac('sha256', prk).update(`${fields[3]}>${fields[4]}|${fields[2]}`).update(Buffer.from([1])).digest();
    const mac = createHmac('sha256', key).update(canonical).digest('base64url');
    return `fe1.${Buffer.from(canonical).toString('base64url')}.${mac}`;
  };
  const opaqueGrant = (id: string) => wire(['fe1', 'grant', grant.kid, 'fa', grant.aud, '-', '-',
    grant.iat, grant.exp, id, grant.kind, grant.org, grant.workspace, grant.user, grant.via,
    grant.sub, grant.scope, '-', '-']);
  const target = opaqueGrant(grant.jti);
  const controlId = randomBytes(16).toString('hex');
  if (controlId === grant.jti) throw new Error('GW009 control identity collision');
  const control = opaqueGrant(controlId);
  return async (positive: boolean, deadline: number) => {
    const remaining = Math.floor(deadline - performance.now());
    if (remaining <= 0) throw new Error('GW009 delegated refusal exceeded the 75 s healthy-sync bound');
    const id = positive ? controlId : grant.jti;
    const token = positive ? control : target;
    const now = Math.floor(Date.now() / 1000);
    // Do not let expiration become the reason the negative side passes.
    if (Number(grant.iat) > now || Number(grant.exp) <= now + 60) {
      throw new Error('GW009 grant lifetime cannot cover the revocation proof');
    }
    const envelope = wire(['fe1', 'request', 'k1', 'fa', 'gw-delegated', 'GET', '/-/batches',
      String(now), String(now + 60), randomBytes(16).toString('hex'), '-', grant.org,
      grant.workspace, grant.user, `grant:${id}`, '-', 'batch', digest(''), digest(token)]);
    try {
      // No bearer/API key, no query or body, no redirects, fresh nonce per call.
      const response = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(Math.min(5000, remaining)),
        headers: { 'X-FA-Envelope': envelope, 'X-FA-Grant': token, 'Cache-Control': 'no-store' } });
      const body = await response.json() as { error?: { code?: string };
        batches?: { batch_id?: string; org_id?: string; key_id?: string }[] };
      const listed = Array.isArray(body.batches) && body.batches.some(row => row.batch_id === batchId
        && row.org_id === grant.org && row.key_id === `dlg_${grant.jti}`)
        && body.batches.every(row => row.org_id === grant.org);
      return { status: response.status, listed,
        rejected: body.error?.code === 'service_envelope_rejected', inWindow: performance.now() <= deadline };
    } catch {
      // Never let a transport/JSON error expose request headers or response data.
      throw new Error('GW009 delegated list transport or response failed (details withheld)');
    }
  };
}

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-009: a console batch job runs as the organization: served by its own provider, logged and announced as its, refused to a member, hidden from another organization',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-009',
      area: 'gateway',
      userGoal:
        'An org admin runs a console batch job that is served by the organization\'s own provider, logged and announced as the organization\'s, while a member can only watch: in the console Submit Batch Job and Health Check are an Owner or Admin\'s, and the member\'s own health check request re-pushes nothing; another organization cannot see or cancel it. Narrowing (REL-R7 E2E plan, round 1 Q1): the row\'s "trace id" clause is not proven here; GW-E2E-018 carries it on the r7alt profile, the only one whose request log keeps request headers',
      steps: [
        'seed the org\'s own provider on the mock, an org key, and a webhook subscribed to batch.completed',
        'as a member, open Gateway Settings, then Batch Jobs, and see Submit Batch Job and Health Check disabled, Owner or Admin only',
        'as the member, wait until the gateway\'s copy of the org config carries no push stamp, then send the member\'s own health check request and reload Settings',
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
        'the batch\'s ServiceGrant is revoked and the job stamped; a correctly signed delegated list is refused within the 75 s healthy-sync bound, beside successful same-shape controls',
      ],
      browserChecks: [
        'the Member sees Submit Batch Job disabled with "Owner or Admin only" and the Member empty state',
        'the Member sees Health Check disabled with "Owner or Admin only"',
        'after the Member\'s own health check request, a reloaded Settings shows Last Health Check ending "(healthy)"',
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
    const model = `e2e-gw9-model-${suffix}`;
    const providerKey = syntheticProviderKey([model]);
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
    // Gateway Info is a section named by its title, its rows a dt/dd list
    // (settings/ui/SettingsPanel.jsx, settings/ui/KeyValueList.jsx).
    const lastHealthCheck = (p: Page) => p.getByRole('region', { name: 'Gateway Info', exact: true })
      .getByText('Last Health Check', { exact: true }).locator('xpath=following-sibling::dd[1]');
    let batchId = '';
    let keyId = '';
    let repush: StackLock | undefined;
    try {
      const memberPage = await memberContext.newPage();
      await test.step('UI: a member sees Submit Batch Job and Health Check disabled, Owner or Admin only', async () => {
        await memberPage.goto(BATCH_JOBS_PATH, { waitUntil: 'domcontentloaded' });
        const submit = memberPage.getByRole('button', { name: 'Submit Batch Job', exact: true });
        await expect(submit).toBeDisabled({ timeout: UI_READY });
        await expect(memberPage.getByLabel(GATEWAY_ADMIN_ONLY, { exact: true }).filter({ has: submit })).toHaveCount(1);
        // This flow needs delegated batch submission ON. Say so plainly instead of timing
        // out on the empty-state text, which reads differently when it is off.
        const submitOff = memberPage.getByText(BATCH_SUBMIT_OFF, { exact: true });
        const memberEmpty = memberPage.getByText(MEMBER_EMPTY, { exact: true });
        await expect(memberEmpty.or(submitOff).first()).toBeVisible({ timeout: UI_READY });
        if (await submitOff.count() > 0) {
          throw new Error('GW-E2E-009 needs delegated batch submission on (AGENTCC_DELEGATED_BATCH_ENABLED=true on this stack); the console reports "' + BATCH_SUBMIT_OFF + '", so Submit Batch Job is disabled for the admin as well.');
        }
        await expect(memberEmpty).toBeVisible({ timeout: UI_READY });
        // The header's Health Check is the page's only one; the member's is disabled
        // and says who may use it.
        const healthCheck = memberPage.getByRole('button', { name: 'Health Check', exact: true });
        await expect(healthCheck).toHaveCount(1);
        await expect(healthCheck).toBeDisabled({ timeout: UI_READY });
        await expect(memberPage.getByLabel(GATEWAY_ADMIN_ONLY, { exact: true }).filter({ has: healthCheck })).toHaveCount(1);
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

      // H9: no other flow's admin Health Check may re-push every org's config inside #3's window.
      repush = await StackLock.acquire(STACK_LOCKS.ORG_CONFIG_REPUSH, { timeoutMs: REPUSH_LOCK_WAIT });
      await testInfo.attach('repush-lock', { contentType: 'application/json', body: JSON.stringify({ waitedMs: repush.waitedMs }) });
      await test.step('API + gateway: the member\'s own health check works and re-pushes nothing', async () => {
        // Precondition: the provider create's push stamp has been cleared by a sync, so a
        // stamp after the request could only come from a re-push by it.
        await expect.poll(pushStamp, POLL.GATEWAY_SYNC).toBe('unstamped');
        const checked = await scopes.send<{ result: { org_configs_pushed: unknown } }>(member, 'POST', HEALTH_CHECK, {});
        expect(checked.status).toBe(200);
        expect({ pushed: checked.body.result.org_configs_pushed, stamp: await pushStamp() })
          .toEqual({ pushed: false, stamp: 'unstamped' });
        // A full navigation: the check is recorded for the org, and Settings reads it.
        await memberPage.goto(SETTINGS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(lastHealthCheck(memberPage)).toHaveText(/ \(healthy\)$/, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET + POLL.GATEWAY_SYNC.timeout });
      await repush.release();

      const page = await ownerContext.newPage();
      const row = () => page.locator('tbody tr').filter({ hasText: batchId });
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
        await expect(page.locator('tbody tr td:first-child')).toHaveText([batchId], { timeout: UI_READY });
        await expect(row().locator('.MuiChip-label')).toHaveText(['completed'], { timeout: BATCH_DONE.timeout });
      }, { timeout: STEP_BUDGET + BATCH_DONE.timeout });

      // Run immediately after completed, not after the UI/log/webhook checks:
      // their duration must not silently enlarge the product's revocation window.
      await test.step('storage + gateway: the completed batch grant is revoked and actually refused', async () => {
        const stepDeadline = performance.now() + POLL.GATEWAY_SYNC.timeout;
        let deadline = stepDeadline;
        let grant: BatchGrant | undefined;
        await expect.poll(async () => {
          const started = performance.now();
          // Claim bytes stay in memory. Assertions/diagnostics contain booleans only.
          // accounts/models/service_grant.py:13-39; delegated_batches.py:135-143.
          const rows = await probe.pg<BatchGrant & { revoked: boolean; recorded: boolean;
            batch_ended: boolean; age_ms: number | null }>(
            `SELECT g.jti, g.kid, g.aud, g.kind, g.org::text, COALESCE(g.workspace::text, '-') AS workspace,
                    g."user"::text AS "user", g.via, g.sub, g.scope, g.iat::text, g.exp::text,
                    g.revoked_at IS NOT NULL AS revoked, j.key_revoked_at IS NOT NULL AS recorded,
                    g.revoked_reason = 'batch_ended' AS batch_ended,
                    (EXTRACT(EPOCH FROM (clock_timestamp() - g.revoked_at)) * 1000)::double precision AS age_ms
               FROM agentcc_batch_job j JOIN accounts_service_grant g
                 ON g.batch_ref = j.id AND g.org = j.organization_id AND g."user" = j.created_by_id
                AND j.gateway_key_id = 'dlg_' || g.jti AND g.sub = 'batch:' || j.id::text
              WHERE j.gateway_batch_id = $1 AND j.organization_id = $2 AND j.created_by_id = $3
                AND g.kind = 'batch' AND g.jti ~ '^[0-9a-f]{32}$'`,
            [batchId, owner.organizationId, owner.userId]);
          const row = rows[0];
          if (rows.length === 1 && row.revoked && row.recorded && row.batch_ended && row.age_ms !== null) {
            grant = row;
            // Use the stable grant.revoked_at, NOT the repeatedly stamped job field.
            // Subtract DB age and query time conservatively; no fresh sync-window
            // allowance starts when the test happens to notice the revocation.
            deadline = Math.min(stepDeadline, started + GRANT_REVOCATION_WINDOW_MS - row.age_ms);
          }
          return { one: rows.length === 1, revoked: row?.revoked === true,
            recorded: row?.recorded === true, batchEnded: row?.batch_ended === true };
        }, POLL.ASYNC_JOB).toEqual({ one: true, revoked: true, recorded: true, batchEnded: true });
        if (!grant || !Number.isFinite(deadline) || deadline <= performance.now()) {
          throw new Error('GW009 did not observe revocation in time to prove the 75 s healthy-sync bound');
        }
        const list = delegatedListProbe(grant, batchId);
        let consecutive = 0;
        await expect.poll(async () => {
          // Both controls must succeed, including org scoping and the actual batch
          // identity. A broken signer, expired grant, replay, missing route/member,
          // or stale snapshot cannot stand in for a revocation assertion.
          const before = await list(true, deadline);
          expect(before).toEqual({ status: 200, listed: true, rejected: false, inWindow: true });
          const target = await list(false, deadline);
          const after = await list(true, deadline);
          expect(after).toEqual({ status: 200, listed: true, rejected: false, inWindow: true });
          const refused = target.status === 401 && target.rejected && !target.listed && target.inWindow;
          consecutive = refused ? consecutive + 1 : 0;
          return consecutive;
        }, { timeout: Math.max(1, deadline - performance.now()), intervals: [250, 500, 1000] }).toBe(2);
      });

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
        await testInfo.attach('rows', { contentType: 'application/json', body: JSON.stringify({ requestIds }) });
        await expect.poll(async () => probe.pg<{ request_id: string; principal_matches: boolean }>(
          `SELECT request_id, api_key_id = $3 AS principal_matches, provider, status_code FROM agentcc_request_log
            WHERE organization_id = $1 AND model = $2 ORDER BY request_id`, [owner.organizationId, model, keyId]), LOG_VISIBLE)
          .toEqual(requestIds.map((request_id) => ({ request_id, principal_matches: true, provider: providerName, status_code: 200 })));
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
        const seen = (await upstream.requests(scenario)).map((r) => [r.headers.authorization === `Bearer ${providerKey}`,
          (r.body as { messages: { content: string }[] }).messages.at(-1)?.content]).sort();
        expect(seen).toEqual([[true, `e2e-gw9-${suffix}-a`], [true, `e2e-gw9-${suffix}-b`]]);
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

    } finally {
      await admin.dispose();
      await memberContext.close();
      await ownerContext.close();
      await repush?.release();
    }
  },
);
