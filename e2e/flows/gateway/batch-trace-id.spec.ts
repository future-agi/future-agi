import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { profileTag } from '../../lib/profile';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - no agentcc_request_log column holds a trace id: the flusher sends it only inside
//   agent.trace_id (agentcc-gateway/internal/plugins/logging/flusher.go :148, :507)
//   and agentcc/services/log_ingestion.py _AGENT_COLUMNS does not store it; the
//   inbound X-Agentcc-Trace-Id reaches the row only inside request_headers, which
//   the gateway sends only with logging.request_logging.include_bodies on: the
//   alt profile (e2e/lib/profile.ts ALT_DELTA), off on stock.
// - stored shape: a one-element JSON array under the canonical key
//   (internal/server/handlers.go:177-178 cloneRequestHeaders takes r.Header.Clone();
//   plugins/logging/record.go:63 RequestHeaders map[string][]string; flusher.go
//   :585-587 marshals it; log_ingestion.py:842 _sanitize_headers ->
//   credential_headers.py:184-187 keeps every non-credential value), into the jsonb
//   request_headers (agentcc/models/request_log.py:61); so the id is read with
//   ->'X-Agentcc-Trace-Id'->>0 (->> on the key would give the array's text).
// - the batch's trace id is minted by the gateway at submit
//   (internal/middleware/requestid.go:63-67: the caller's x-agentcc-trace-id, else a
//   new ULID) and carried by every item (internal/server/server.go:1053 SubmitAs,
//   internal/batch/run_as_request.go:40-41); no other surface shows it (the Batch
//   JSON has no trace field, internal/batch/batch.go; Django sends none and reads no
//   submit header, agentcc/services/gateway_client.py submit_batch). Request ids and
//   minted trace ids are ULIDs (requestid.go:27-29 generateULID, oklog/ulid/v2:
//   Crockford base32, 26 characters); a batch item runs through the same middleware
//   (run_as_request.go:16-24, :54, :57).
// - the console and wire as GW-E2E-009 (GatewaySettingsSection.jsx Batch Jobs,
//   "Submit Batch Job", the dialog, the status chip; POST
//   /agentcc/gateways/default/submit-batch/ { requests, max_concurrency: 5 }); wire
//   bodies for the provider and key as GW-E2E-009.
// Proven on the alt profile only; the batch's trace id is proven as one well-formed
// id carried once by both items, distinct from each item's request id and from an
// independent request's trace id, because no surface shows the batch's own trace id.
const BATCH_JOBS_PATH = '/dashboard/gateway/settings/batch-jobs';
const SUBMIT_BATCH_PATH = /\/agentcc\/gateways\/[^/]+\/submit-batch\/$/;
const ULID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy
const BATCH_DONE = POLL.ASYNC_JOB; // a console batch's items finishing on the echo mock
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
// provisioning 120 + seeding 15 + control call 2 + Admin submit step 75 + B1 BATCH_DONE
// 60 + rows precondition LOG_VISIBLE 60 + #1 1 + headroom 30 = 363 s, rounded up.
// (The push precondition before the control call shares the seeding's share.)
const FLOW_TIMEOUT = 420_000;

type Created<T> = { result: T };
type TraceRow = { request_id: string; n: number | null; trace_id: string | null };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-018: a console batch job\'s items share one trace id in the request log, the batch\'s, carried by no other request',
  {
    tag: ['@flow', profileTag('alt')],
    annotation: flowAnnotation({
      id: 'GW-E2E-018',
      area: 'gateway',
      userGoal:
        'An org admin who runs a console batch job on a gateway that logs request headers can find both of its items in the request log under one trace id that no other request carries. It is proven on the alt gateway profile only, because the trace id reaches the request log only inside request_headers, which a stock gateway does not send; and the batch\'s trace id is proven as one well-formed id carried once by both items, distinct from each item\'s request id and from an independent request\'s trace id, because no surface shows the batch\'s own trace id',
      steps: [
        'seed the org\'s own provider on the mock and an org key',
        'send one independent request with the org key and keep its trace id and request id',
        'as the admin, open Batch Jobs, submit two requests for the org\'s model, and watch the job reach completed',
        'wait until the request log holds the batch\'s two rows besides the independent one',
      ],
      backendChecks: [
        'both batch items are logged under one trace id, the batch\'s: present on both rows, carried once on each, well formed, the same on both, not either item\'s request id, and not the trace id of an independent request',
      ],
      browserChecks: [
        'the admin\'s job row shows completed',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // The model is this flow's alone, so its three request log rows are the only rows
    // under it; the control is told apart by its own request id.
    const scenario = `gw18-${suffix}`;
    const providerName = `e2e-gw18-${suffix}`;
    const model = `e2e-gw18-model-${suffix}`;
    const itemA = { model, messages: [{ role: 'user', content: `e2e-gw18-${suffix}-a` }] };
    const itemB = { model, messages: [{ role: 'user', content: `e2e-gw18-${suffix}-b` }] };

    const upstream = new MockUpstream();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw18-${suffix}` }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [model], extra_config: { api_path_prefix: '/v1' },
    });
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/',
      { name: `e2e-gw18-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, model, organizationId: owner.organizationId,
        providerId: provider.result.id, gatewayKeyId: key.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    let control = { traceId: '', requestId: '' };
    try {
      await test.step('caller: one independent request with the org key', async () => {
        // Precondition: the provider create's push reached the gateway.
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toContain(providerName);
        const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
          headers: { Authorization: `Bearer ${key.result.key}` },
          data: { model, messages: [{ role: 'user', content: `e2e-gw18-${suffix}-c` }] },
        });
        expect(res.status(), await res.text()).toBe(200);
        control = { traceId: res.headers()['x-agentcc-trace-id'] ?? '', requestId: res.headers()['x-agentcc-request-id'] ?? '' };
        await testInfo.attach('call', { contentType: 'application/json', body: JSON.stringify(control) });
      });
    } finally {
      await admin.dispose();
      await caller.dispose();
    }

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    let batchId = '';
    try {
      await test.step('UI: the admin submits a two-request batch and watches it complete', async () => {
        await page.goto(BATCH_JOBS_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Submit Batch Job', exact: true }).click({ timeout: UI_READY });
        const dialog = page.getByRole('dialog', { name: 'Submit Batch Job' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        await dialog.getByRole('textbox').fill(JSON.stringify([itemA, itemB], null, 2));
        const submitted = page.waitForResponse(
          (r) => r.request().method() === 'POST' && SUBMIT_BATCH_PATH.test(new URL(r.url()).pathname), { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Submit', exact: true }).click();
        const response = await submitted;
        expect(response.status()).toBe(200);
        batchId = ((await response.json()) as { result: { batch_id: string } }).result.batch_id;
        await testInfo.attach('batch', { contentType: 'application/json', body: JSON.stringify({ batchId }) });
        const row = page.locator('tbody tr').filter({ hasText: `${batchId.slice(0, 12)}...` });
        await expect(row.locator('.MuiChip-label')).toHaveText(['completed'], { timeout: BATCH_DONE.timeout });
      }, { timeout: STEP_BUDGET + BATCH_DONE.timeout });
    } finally {
      await context.close();
    }

    await test.step('storage: both items carry the batch\'s one trace id, and no other request does', async () => {
      const q18 = () => probe.pg<TraceRow>(
        `SELECT request_id,
                CASE WHEN jsonb_typeof(request_headers->'X-Agentcc-Trace-Id') = 'array'
                     THEN jsonb_array_length(request_headers->'X-Agentcc-Trace-Id') END AS n,
                request_headers->'X-Agentcc-Trace-Id'->>0 AS trace_id
           FROM agentcc_request_log
          WHERE organization_id = $1 AND model = $2 AND request_id <> $3
          ORDER BY request_id`, [owner.organizationId, model, control.requestId]);
      // Precondition: the batch's two rows have been ingested.
      await expect.poll(async () => (await q18()).length, LOG_VISIBLE).toBe(2);
      const rows = await q18();
      await testInfo.attach('rows', { contentType: 'application/json', body: JSON.stringify({ rows, control }) });
      const [r1, r2] = rows;
      const c = control.traceId;
      const shapes = [r1.trace_id, r2.trace_id, r1.request_id, r2.request_id, c]
        .every((v) => typeof v === 'string' && ULID.test(v));
      const ids = [r1.request_id, r2.request_id];
      expect({
        present: r1.trace_id !== null && r2.trace_id !== null,
        arrayLength1: r1.n === 1 && r2.n === 1,
        ulidShape: shapes,
        same: shapes && r1.trace_id === r2.trace_id,
        notARequestId: shapes && !ids.includes(r1.trace_id as string) && !ids.includes(r2.trace_id as string),
        notTheControl: shapes && r1.trace_id !== c && r2.trace_id !== c,
      }).toEqual({ present: true, arrayLength1: true, ulidShape: true, same: true, notARequestId: true, notTheControl: true });
    });
  },
);
