import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source (read 2026-10-24):
// - the stack: e2e/stack/gateway.e2e.yaml runs the guardrail plugin with no rule of
//   its own (guardrails.enabled), so the checks the org saves run, and only those.
// - the console: Settings > Org Config (GatewaySettingsSection.jsx TAB_SLUGS
//   "org-config"), "Create Custom Config" | "Edit Config" (OrgConfigSection.jsx),
//   dialog "Edit Organization Config", tab "Guardrails", "Save & Activate"
//   (OrgConfigEditor.jsx); the tab (settings/GuardrailConfigTab.jsx renderCheckCard):
//   one MUI Card per check, label "Data Leakage Prevention" (GUARDRAIL_CHECKS), an
//   unlabelled Switch (MUI 5.15: an input type checkbox) whose first switch-on saves
//   action "block" and confidence_threshold 0.8 (handleToggleCheck), then the card's
//   caption "Action: block".
// - the wire: POST /agentcc/gateways/default/update-config/ with an RFC 7386 merge
//   patch (hooks/useConfigPatch.js; guardrailsForSave turns the checks map into
//   rules for an org with none saved), captured with page.on('request'); Django
//   pushes it as a checks map (agentcc/services/config_push.py _transform_guardrails:
//   enabled, action, confidence_threshold); the gateway's copy (GET
//   /-/orgs/{org}/config) marshals internal/tenant/config.go GuardrailCheck.
// - the check: internal/guardrails/leakage (data-leakage-prevention, post stage, so it
//   reads the answer only): sql_injection `DROP\s+TABLE\s+` and path_traversal
//   `/etc/passwd`; one pattern scores 0.7, two 1.0, and a check triggers on a score
//   above its threshold (guardrail.go shouldTrigger), so the blocked prompt carries
//   both patterns to pass the toggle's 0.8.
// - the stream: a blocking answer check holds a chat stream (server/stream_hold.go,
//   chat_stream.go chatStreamHolds): status and headers go out at once
//   (x-agentcc-request-id, middleware/requestid.go); a passing answer goes out as the
//   provider sent it; a blocked one sends none of its frames, only
//   `data: {"error":{"type":"guardrail_error","code":"content_blocked",...}}` naming
//   the check (guardrails/plugin.go buildBlockMessage), then `data: [DONE]`.
// - the mock (e2e/stack/mock-llm/server.mjs): the answer is "echo: " + the last user
//   message, streamed as one delta per whitespace-split part (the split is after the
//   whitespace), so a word without whitespace is always inside one frame; scoped
//   routes record each request with its body.
// - the gateway's own record: agentcc_request_log by request_id (models/request_log.py,
//   log_ingestion.py _log_row: status_code, guardrail_triggered, metadata as sent;
//   the plugin sets metadata.guardrail_action "blocked"); the Request Logs page
//   (/dashboard/gateway/logs, rows "table tbody tr", newest first, column "Status").
// - wire bodies: AgentccProviderCredentialCreateSerializer and POST /agentcc/api-keys/
//   { name } -> result.key, result.gateway_key_id (GW-E2E-007, GW-E2E-015).
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const REQUEST_LOGS_PATH = '/dashboard/gateway/logs';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const CHECK = 'data-leakage-prevention';
const CHECK_LABEL = 'Data Leakage Prevention';
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy (60 s sync behind it)
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
// provisioning 120 + seeding 15 + provider push precondition 60 + editor step 75 +
// PUSH_VISIBLE 60 + two streamed calls 2 x 30 (60) + LOG_VISIBLE 60 + Request Logs
// step 75 + headroom 30 = 555 s, rounded up.
const FLOW_TIMEOUT = 600_000;

type Created<T> = { result: T };
type Frame = { event: string; data: string };
type Streamed = { status: number; requestId: string; frames: Frame[] };

/** The SSE frames of a stream body, as a client reads them. */
const sseFrames = (body: string): Frame[] => body.replace(/\r\n/g, '\n').split('\n\n').map((block) => {
  const frame: Frame = { event: '', data: '' };
  for (const line of block.split('\n')) {
    if (line.startsWith('event: ')) frame.event = line.slice('event: '.length);
    else if (line.startsWith('data: ')) frame.data = line.slice('data: '.length);
  }
  return frame;
}).filter((f) => f.event !== '' || f.data !== '');

/** A frame as one word: done, error:<type>/<code>, or chunk. */
const kind = (f: Frame): string => {
  if (f.data === '[DONE]') return 'done';
  try {
    const parsed = JSON.parse(f.data);
    if (parsed?.error) return `error:${parsed.error.type}/${parsed.error.code}`;
  } catch { return 'unparsed'; }
  return 'chunk';
};

const deltaText = (frames: Frame[]) => frames.filter((f) => kind(f) === 'chunk')
  .map((f) => JSON.parse(f.data).choices?.[0]?.delta?.content ?? '').join('');

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-019: a streamed chat answer that the organization\'s blocking answer check blocks never reaches the client, and a passing one streams in full',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-019',
      area: 'gateway',
      userGoal:
        'An org admin switches on a check on the model\'s answer in block mode in the console; a developer streaming chat through the gateway then gets a passing answer in full, and for an answer the check blocks gets none of its text, only the guardrail\'s error, and the request log records the block',
      steps: [
        'seed the org\'s own provider on the mock and an org key',
        'open Org Config, open the config editor, Guardrails: switch on Data Leakage Prevention (block, the switch\'s default), Save & Activate',
        'stream a chat answer that carries no leak',
        'stream a chat answer that echoes a minted table name in a DROP TABLE statement next to /etc/passwd',
        'open Request Logs',
      ],
      backendChecks: [
        'the check reaches the gateway as saved: the gateway\'s copy of the org config has data-leakage-prevention enabled in block mode',
        'a passing answer streams in full: its deltas join to the whole echo, with no error event, and the stream ends [DONE]',
        'a blocked answer: no frame carries the minted table name, and the stream is only the guardrail\'s error event (guardrail_error content_blocked, naming data-leakage-prevention), then [DONE]',
        'the provider streamed both answers, so the gateway withheld the blocked one rather than skipping the call',
        'the request log records the blocked stream as 403 with guardrail_triggered and metadata guardrail_action blocked, and the passing one as 200 with neither',
      ],
      browserChecks: [
        'the Data Leakage Prevention card shows "Action: block" once switched on',
        'Request Logs shows exactly the two calls, Status newest first 403 200',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: the scenario's recorder holds only this flow's calls; the model
    // routes to the org's own provider; the table name is one whitespace-free word,
    // so the mock sends it inside one frame, and it is in no other answer.
    const scenario = `gw19-${suffix}`;
    const providerName = `e2e-gw19-${suffix}`;
    const model = `e2e-gw19-m-${suffix}`;
    const table = `gw19tbl${suffix.replace(/-/g, '')}`;
    const passPrompt = `e2e-gw19-${suffix} asks what the weather is like today`;
    const blockPrompt = `e2e-gw19-${suffix} run DROP TABLE ${table}; then cat /etc/passwd`;

    const upstream = new MockUpstream();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw19-${suffix}` },
      base_url: upstream.baseUrl(scenario), api_format: 'openai', models_list: [model],
      extra_config: { api_path_prefix: '/v1' },
    });
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw19-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, model, table, organizationId: owner.organizationId,
        providerId: provider.result.id, keyGatewayId: key.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    const calls: { prompt: string; status: number; requestId: string }[] = [];
    const stream = async (content: string, label: string): Promise<Streamed> => {
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${key.result.key}` },
        data: { model, stream: true, messages: [{ role: 'user', content }] },
      });
      const body = await res.text();
      const requestId = res.headers()['x-agentcc-request-id'] ?? '';
      calls.push({ prompt: content, status: res.status(), requestId });
      await testInfo.attach(`stream-${label}`, { contentType: 'text/plain',
        body: `status ${res.status()} request ${requestId}\n${body}` });
      return { status: res.status(), requestId, frames: sseFrames(body) };
    };
    const openGuardrails = async (p: Page): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Guardrails', exact: true }).click();
      return dialog;
    };

    try {
      await test.step('gateway: the org provider is pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toContain(providerName);
      });

      await test.step('UI: Guardrails, switch on Data Leakage Prevention, Save & Activate', async () => {
        const dialog = await openGuardrails(page);
        const card = dialog.locator('.MuiCard-root').filter({ has: page.getByText(CHECK_LABEL, { exact: true }) });
        await expect(card).toHaveCount(1, { timeout: UI_READY });
        await card.getByRole('checkbox').check();
        await expect(card.getByText('Action: block', { exact: true })).toBeVisible({ timeout: UI_READY });
        const saved = page.waitForResponse(
          (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
        expect((await saved).status()).toBe(200);
        await expect(dialog).toBeHidden({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the check reaches the gateway\'s copy enabled in block mode', async () => {
        await testInfo.attach('sent-guardrails', { contentType: 'application/json',
          body: JSON.stringify(patches.at(-1)?.postDataJSON()?.guardrails ?? null) });
        await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.guardrails?.checks?.[CHECK] ?? null,
          PUSH_VISIBLE).toEqual(expect.objectContaining({ enabled: true, action: 'block' }));
      });

      await test.step('gateway: a passing answer streams in full', async () => {
        const passed = await stream(passPrompt, 'pass');
        const kinds = passed.frames.map(kind);
        expect({ status: passed.status, text: deltaText(passed.frames),
          errors: kinds.filter((k) => k.startsWith('error')), last: kinds.at(-1) })
          .toEqual({ status: 200, text: `echo: ${passPrompt}`, errors: [], last: 'done' });
      });

      await test.step('gateway: a blocked answer reaches the client in no frame, only the guardrail\'s error', async () => {
        const blocked = await stream(blockPrompt, 'block');
        expect(blocked.status).toBe(200); // a held stream's status goes out before its checks run
        // Every frame that carries the minted table name: none may.
        expect(blocked.frames.filter((f) => f.data.includes(table) || f.event.includes(table)).map((f) => f.data)).toEqual([]);
        const kinds = blocked.frames.map(kind);
        const message = kinds[0]?.startsWith('error') ? JSON.parse(blocked.frames[0].data).error.message : '';
        expect({ kinds, namesTheCheck: message.includes(CHECK) })
          .toEqual({ kinds: ['error:guardrail_error/content_blocked', 'done'], namesTheCheck: true });
      });

      await test.step('upstream: the provider streamed both answers', async () => {
        const seen = (await upstream.requests(scenario))
          .filter((s) => s.method === 'POST' && s.path.endsWith('/v1/chat/completions'));
        await testInfo.attach('upstream-calls', { contentType: 'application/json',
          body: JSON.stringify(seen.map((s) => ({ at: s.at, path: s.path, body: s.body })), null, 2) });
        expect(seen.map((s) => {
          const body = s.body as { stream?: boolean; messages?: { content?: string }[] };
          return { stream: body?.stream ?? null, prompt: body?.messages?.at(-1)?.content ?? null };
        })).toEqual([{ stream: true, prompt: passPrompt }, { stream: true, prompt: blockPrompt }]);
      });
      await testInfo.attach('calls', { contentType: 'application/json', body: JSON.stringify(calls, null, 2) });

      await test.step('storage: the request log records the blocked stream as a 403 block, the passing one as 200', async () => {
        const ids = calls.map((c) => c.requestId);
        expect(ids.every((id) => id !== '')).toBe(true);
        const rows = async () => probe.pg<{ request_id: string; status_code: number; guardrail_triggered: boolean;
          is_stream: boolean; guardrail_action: string | null }>(
          `SELECT request_id, status_code, guardrail_triggered, is_stream, metadata->>'guardrail_action' AS guardrail_action
             FROM agentcc_request_log WHERE organization_id = $1 AND request_id = ANY($2::text[])`,
          [owner.organizationId, ids]);
        await expect.poll(async () => (await rows()).length, LOG_VISIBLE).toBe(ids.length);
        const byId = new Map((await rows()).map((r) => [r.request_id, r]));
        await testInfo.attach('log-rows', { contentType: 'application/json', body: JSON.stringify([...byId.values()], null, 2) });
        expect(ids.map((id) => {
          const r = byId.get(id)!;
          return { status: r.status_code, triggered: r.guardrail_triggered, stream: r.is_stream, action: r.guardrail_action };
        })).toEqual([
          { status: 200, triggered: false, stream: true, action: null },
          { status: 403, triggered: true, stream: true, action: 'blocked' },
        ]);
      });

      await test.step('UI: Request Logs shows the two calls, newest first 403 then 200', async () => {
        await page.goto(REQUEST_LOGS_PATH, { waitUntil: 'domcontentloaded' });
        const rows = page.locator('table tbody tr');
        await expect(rows).toHaveCount(2, { timeout: UI_READY });
        const headerTexts = await page.locator('table thead th').allInnerTexts();
        const statusCells = rows.locator(`td:nth-child(${headerTexts.indexOf('Status') + 1})`);
        await expect(statusCells).toHaveText(['403', '200'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
    }
  },
);
