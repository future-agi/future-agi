import { request as pwRequest } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { test, expect } from '../lib/scope-actors';
import type { ScopeActor } from '../lib/scope-actors';
import { ApiError } from '../lib/api-client';
import { E2E } from '../lib/env';

// Opt-in live run of TypeSafe's Jev decision models through the whole
// product: a TypeSafe provider added in the dashboard, a gateway key,
// POST /v1/systemone, Request Logs and cost. It calls TypeSafe's live API and
// spends real money, so it is skipped unless both are set:
//   E2E_TYPESAFE_LIVE=1  TYPESAFE_API_KEY=<a TypeSafe test key with a spend cap>
// TYPESAFE_BASE_URL overrides the upstream for a dry run against the stack's
// mock (http://mock-llm:8080 with TYPESAFE_API_KEY=e2e-decision-typesafe-key),
// which also needs AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=true on the backend and
// the gateway. A dry run proves the harness, not TypeSafe.
//
// It writes test-results/typesafe-live/report.{json,md}: per case the request
// (a long state truncated), the response, the Request Logs row or "no row",
// the expected cost and a verdict. Keys are never written: not the TypeSafe
// key, not the gateway keys. Findings are soft assertions, so every case runs
// and the report is complete; any FAIL fails the test.
//
// Budget: about 40 upstream calls and 45k input tokens per run (< $0.01).

const LIVE = process.env.E2E_TYPESAFE_LIVE === '1';
const TYPESAFE_KEY = process.env.TYPESAFE_API_KEY ?? '';
const BASE_URL = process.env.TYPESAFE_BASE_URL ?? '';
const DRY_RUN = BASE_URL !== '' && new URL(BASE_URL).hostname !== 'api.typesafe.ai';
// docker-compose.distributed.yml AGENTCC_ADMIN_TOKEN default; see provider-path-prefix.spec.ts.
const GATEWAY_ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
// internal/modeldb/decision_models.go: Jev input $0.042 per million tokens, output $0.
const INPUT_PER_TOKEN = 0.042e-6;
const OUT_DIR = fileURLToPath(new URL('../test-results/typesafe-live', import.meta.url));
const UI_READY = 60_000;
const LOG_VISIBLE = { timeout: 90_000, intervals: [2000, 5000] };
const CONFIG_VISIBLE = { timeout: 120_000, intervals: [2000, 5000] };
const PROVIDER = 'typesafe';
const BAD_KEY_PROVIDER = 'typesafe-badkey';

type Verdict = 'PASS' | 'FAIL' | 'NOTE';
interface CallRecord {
  label: string;
  request: { route: string; model?: string; body: unknown };
  status: number;
  headers: Record<string, string>;
  body: unknown;
  logRow: Record<string, unknown> | 'no row' | 'not checked';
  expectedCost?: string;
}
interface CaseRecord {
  id: string; title: string; verdict: Verdict; ticket: string; actual: string;
  calls: CallRecord[];
}
interface BugDraft { title: string; repro: string; expected: string; actual: string; suspect: string; severity: string }

const report = {
  header: {
    mode: DRY_RUN ? 'dry-run (mock upstream)' : 'live',
    devSha: process.env.E2E_DEV_SHA ?? 'unset (export E2E_DEV_SHA=$(git rev-parse HEAD))',
    images: process.env.E2E_IMAGE_TAGS ?? 'unset (export E2E_IMAGE_TAGS)',
    startedAt: new Date().toISOString(),
    typesafeEndpoint: BASE_URL || 'https://api.typesafe.ai (preset)',
    upstreamCalls: 0,
    upstreamInputTokens: 0,
  },
  cases: [] as CaseRecord[],
  bugDrafts: [] as BugDraft[],
  specQuestions: [] as string[],
};

const secrets: string[] = [];
function redact(text: string): string {
  let out = text;
  for (const s of secrets) if (s) out = out.split(s).join('<redacted>');
  return out;
}
function safe<T>(value: T): T {
  return JSON.parse(redact(JSON.stringify(value ?? null))) as T;
}
function truncateState(body: unknown): unknown {
  if (!body || typeof body !== 'object') return body;
  const copy = { ...(body as Record<string, unknown>) };
  if (typeof copy.state === 'string' && copy.state.length > 200) {
    copy.state = `${copy.state.slice(0, 120)}… [${copy.state.length} chars, sha256 ${createHash('sha256').update(copy.state).digest('hex').slice(0, 12)}]`;
  }
  return copy;
}
const expectedCost = (inputTokens: number) => (inputTokens * INPUT_PER_TOKEN).toFixed(6);

// Synthetic states only: made-up support tickets and lorem text.
const TICKET = 'Synthetic support ticket #E2E-4471: the customer says the export button on the reports page '
  + 'does nothing after the latest update; the agent asked for a screenshot and promised a follow-up within a day.';
function largeState(minTokens: number): string {
  const lorem = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore '
    + 'et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip. ';
  // ~4 characters per token; a margin keeps it above minTokens.
  return `${TICKET}\n${lorem.repeat(Math.ceil((minTokens * 4.4) / lorem.length))}`;
}
const Q = {
  noul: { resolved: { type: 'noul', instructions: 'Was the customer promised a follow-up?' } },
  choice: { sentiment: { type: 'choice', instructions: 'What is the customer sentiment?',
    criteria: { positive: 'satisfied or thankful', neutral: 'matter of fact', negative: 'frustrated or upset' } } },
  score: { quality: { type: 'score', instructions: 'Rate the agent reply quality.',
    criteria: ['poor', 'fair', 'good', 'excellent'] } },
};
const ALL = { ...Q.noul, ...Q.choice, ...Q.score };

// The config's retain-on-failure trace would capture the key typed into the dialog.
test.use({ trace: 'off', screenshot: 'off', video: 'off' });

test.describe('TypeSafe Jev live decision route (opt-in)', () => {
  test.skip(!LIVE || !TYPESAFE_KEY,
    'Opt-in: set E2E_TYPESAFE_LIVE=1 and TYPESAFE_API_KEY (a TypeSafe test key) to call TypeSafe live.');

  test('typesafe/jev-latest through dashboard provider, gateway key, /v1/systemone, Request Logs and cost',
    async ({ browser, scopeActors }) => {
      test.setTimeout(1_200_000);
      secrets.push(TYPESAFE_KEY);
      fs.mkdirSync(OUT_DIR, { recursive: true });
      const owner = scopeActors.ownerA;
      const suffix = Date.now().toString(36);
      const context = await scopeActors.openContext(browser, owner);
      const page = await context.newPage();
      const gw = await pwRequest.newContext({ baseURL: E2E.gatewayUrl, timeout: 120_000 });
      const admin = await pwRequest.newContext({ baseURL: E2E.gatewayUrl,
        extraHTTPHeaders: { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` } });

      try {
        await run(page, owner, gw, admin, suffix);
      } finally {
        writeReport();
        await gw.dispose();
        await admin.dispose();
        await context.close();
      }
    });
});

// The status of a dashboard API call that may be refused; never throws on 4xx.
async function tryApi<T>(fn: () => Promise<T>): Promise<{ status: number; body: unknown }> {
  try {
    return { status: 200, body: await fn() };
  } catch (e) {
    if (e instanceof ApiError) return { status: e.status, body: e.body };
    throw e;
  }
}

function addCase(c: CaseRecord) {
  report.cases.push(safe(c));
  expect.soft(c.verdict, `${c.id} ${c.title}: ${redact(c.actual)}`).not.toBe('FAIL');
}

async function run(page: Page, owner: ScopeActor, gw: APIRequestContext, admin: APIRequestContext, suffix: string) {
  // ---------------------------------------------------------------- Case 1
  const providerCfg = await test.step('Case 1: add the TypeSafe provider in the dashboard', async () => {
    await page.goto('/dashboard/gateway/providers', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Add Provider' }).first().click({ timeout: UI_READY });
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Provider').click();
    await page.getByRole('option', { name: 'TypeSafe', exact: true }).click();
    if (BASE_URL) await dialog.getByLabel('Base URL').fill(BASE_URL);
    await dialog.getByLabel('API Key').fill(TYPESAFE_KEY);
    const saved = page.waitForResponse(r => r.url().includes('/agentcc/gateways/') && r.request().method() === 'POST',
      { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Add Provider' }).click();
    const savedStatus = (await saved).status();
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    // The dialog is closed: no key on screen.
    await page.screenshot({ path: `${OUT_DIR}/01-providers.png`, fullPage: true });
    const creds = await owner.api.get<{ result?: unknown; results?: unknown }>('/agentcc/provider-credentials/');
    const list = (Array.isArray(creds) ? creds : (creds.results ?? creds.result ?? [])) as Array<Record<string, unknown>>;
    const typesafe = list.find(p => p.provider_name === PROVIDER);
    addCase({ id: '1a', title: 'Dashboard TypeSafe preset saves a provider', ticket: 'Add a TypeSafe provider with a TypeSafe key',
      verdict: savedStatus === 200 && !!typesafe ? 'PASS' : 'FAIL',
      actual: `save status ${savedStatus}; provider ${typesafe ? `saved, models ${JSON.stringify(typesafe.models_list)}` : 'missing'}`,
      calls: [] });
    return typesafe;
  });
  if (!providerCfg) throw new Error('TypeSafe provider was not saved; cannot continue');

  // A second provider with a wrong key, for the upstream-refusal case.
  await owner.api.post('/agentcc/provider-credentials/', {
    provider_name: BAD_KEY_PROVIDER, credentials: { api_key: `invalid-typesafe-key-${suffix}` },
    base_url: BASE_URL || 'https://api.typesafe.ai', api_format: 'systemone', models_list: ['jev-latest'],
  });
  const key = await owner.api.post<{ result: { id: string; key: string; name: string } }>('/agentcc/api-keys/',
    { name: `e2e-typesafe-${suffix}` });
  secrets.push(key.result.key);
  const auth = { Authorization: `Bearer ${key.result.key}` };

  await test.step('gateway has the org providers and the key', async () => {
    await expect.poll(async () => {
      const res = await admin.get('/-/orgs/configs');
      if (res.status() !== 200) return `status ${res.status()}`;
      const providers = ((await res.json()).data?.[owner.organizationId]?.providers ?? {}) as Record<string, unknown>;
      // Presence only: the response holds the provider keys and is never logged.
      return [PROVIDER, BAD_KEY_PROVIDER].every(p => p in providers) ? 'ready' : 'waiting';
    }, { ...CONFIG_VISIBLE, message: 'org providers reach the gateway' }).toBe('ready');
    await expect.poll(async () => (await gw.get('/v1/decisions/models', { headers: auth })).status(),
      { ...CONFIG_VISIBLE, message: 'gateway key synced' }).toBe(200);
  });

  const call = async (label: string, body: unknown, opts: { headers?: Record<string, string>; raw?: string; upstream?: boolean } = {})
    : Promise<CallRecord> => {
    const res = await gw.post('/v1/systemone', { headers: { ...auth, ...(opts.headers ?? {}), 'Content-Type': 'application/json' },
      data: opts.raw ?? JSON.stringify(body) });
    const text = await res.text();
    let parsed: unknown = text;
    try { parsed = JSON.parse(text); } catch { /* keep text */ }
    const headers = Object.fromEntries(Object.entries(res.headers()).filter(([k]) => k.startsWith('x-agentcc-')));
    if (opts.upstream !== false && res.status() === 200) {
      report.header.upstreamCalls += 1;
      report.header.upstreamInputTokens += Number((parsed as { usage?: { input_tokens?: number } })?.usage?.input_tokens ?? 0);
    } else if (headers['x-agentcc-upstream-status']) {
      report.header.upstreamCalls += 1;
    }
    const model = (body as { model?: string })?.model;
    return { label, request: { route: '/v1/systemone', model, body: truncateState(body ?? opts.raw) },
      status: res.status(), headers, body: parsed, logRow: 'not checked' };
  };
  const logRow = async (requestId: string | undefined, expectRow: boolean) => {
    if (!requestId) return 'no row' as const;
    const find = async () => {
      const page = await owner.api.get<{ results: Array<Record<string, unknown>> }>('/agentcc/request-logs/', { request_id: requestId });
      return page.results ?? [];
    };
    if (expectRow) {
      let rows: Array<Record<string, unknown>> = [];
      await expect.poll(async () => (rows = await find()).length, { ...LOG_VISIBLE, message: `log row for ${requestId}` })
        .toBeGreaterThan(0).catch(() => undefined);
      if (!rows.length) return 'no row' as const;
      const r = rows[0];
      const meta = (r.metadata ?? {}) as Record<string, unknown>;
      return { count: rows.length, id: r.id, model: r.model, resolved_model: r.resolved_model, provider: r.provider,
        status_code: r.status_code, input_tokens: r.input_tokens, output_tokens: r.output_tokens, cost: r.cost,
        latency_ms: r.latency_ms, endpoint_type: meta.endpoint_type, decision_format: meta.decision_format,
        decision_question_count: meta.decision_question_count };
    }
    // A refusal before the pipeline is expected to leave no row; wait out one flush to say so.
    await new Promise(r => setTimeout(r, 12_000));
    const rows = await find();
    return rows.length ? { count: rows.length, status_code: rows[0].status_code, latency_ms: rows[0].latency_ms } : 'no row' as const;
  };
  const errorOf = (c: CallRecord) => ((c.body as { error?: { code?: string; message?: string } })?.error ?? {});

  // ---------------------------------------------------------------- Cases 2–4
  const typed: CallRecord[] = [];
  await test.step('Cases 2–4: question types, unchanged answer, resolved model, Request Logs', async () => {
    const shapes: Array<[string, Record<string, unknown>]> = [['noul', Q.noul], ['choice', Q.choice], ['score', Q.score], ['all three', ALL]];
    for (const [name, questions] of shapes) {
      const c = await call(`jev-latest ${name}`, { model: 'typesafe/jev-latest', state: `${TICKET} e2e-decision:pass`, questions });
      typed.push(c);
    }
    const answerTypes = typed.map(c => {
      const answers = (c.body as { answers?: Record<string, { type?: string }> })?.answers ?? {};
      return `${c.label}: ${c.status} ${Object.entries(answers).map(([k, v]) => `${k}=${v?.type}`).join(',')}`;
    });
    const typesOk = typed.every(c => c.status === 200) && typed.slice(0, 3).every((c, i) => {
      const a = Object.values((c.body as { answers?: Record<string, { type?: string }> }).answers ?? {});
      return a.length === 1 && a[0]?.type === ['noul', 'choice', 'score'][i];
    });
    addCase({ id: '2', title: 'noul, choice, score and a combined call', ticket: 'Each question type: Noul, single choice, rubric score',
      verdict: typesOk ? 'PASS' : 'FAIL', actual: answerTypes.join('; '), calls: typed });

    const shapeIssues: string[] = [];
    for (const c of typed) {
      const b = c.body as Record<string, unknown>;
      const keys = Object.keys(b ?? {}).sort().join(',');
      if (keys !== 'answers,model,usage') shapeIssues.push(`${c.label}: body keys ${keys}`);
      if (c.headers['x-agentcc-provider'] !== PROVIDER) shapeIssues.push(`${c.label}: x-agentcc-provider ${c.headers['x-agentcc-provider']}`);
      if (c.headers['x-agentcc-model-used'] !== b?.model) shapeIssues.push(`${c.label}: x-agentcc-model-used ${c.headers['x-agentcc-model-used']} vs body model ${String(b?.model)}`);
    }
    if (DRY_RUN) {
      // e2e/stack/mock-llm/server.mjs: model echoed, 37 input tokens, pass → noul 0.9, last choice/level.
      const want = { model: 'jev-latest', usage: { input_tokens: 37, output_tokens: 0 },
        answers: { resolved: { type: 'noul', noul: 0.9 } } };
      if (!isDeepStrictEqual(typed[0].body, want)) shapeIssues.push(`noul body differs from the mock's: ${JSON.stringify(typed[0].body)}`);
    }
    addCase({ id: '3', title: 'Answer unchanged; resolved model recorded', ticket: 'The answer comes back unchanged, and the resolved model version is recorded',
      verdict: shapeIssues.length ? 'FAIL' : 'PASS',
      actual: shapeIssues.join('; ') || `body keys answers,model,usage; TypeSafe model "${String((typed[0].body as Record<string, unknown>)?.model)}" = x-agentcc-model-used`, calls: [] });

    const logIssues: string[] = [];
    for (const c of typed) {
      c.logRow = await logRow(c.headers['x-agentcc-request-id'], true);
      const usage = (c.body as { usage?: { input_tokens: number; output_tokens: number } }).usage;
      c.expectedCost = usage ? expectedCost(usage.input_tokens) : undefined;
      if (c.logRow === 'no row') { logIssues.push(`${c.label}: no row`); continue; }
      const r = c.logRow as Record<string, unknown>;
      const checks: Array<[string, unknown, unknown]> = [
        ['rows', r.count, 1], ['model', r.model, 'typesafe/jev-latest'], ['provider', r.provider, PROVIDER],
        ['metadata.endpoint_type', r.endpoint_type, 'decision'], ['input_tokens', r.input_tokens, usage?.input_tokens],
        ['output_tokens', r.output_tokens, usage?.output_tokens], ['resolved_model', r.resolved_model, (c.body as Record<string, unknown>).model],
        ['cost', Number(r.cost).toFixed(6), c.expectedCost], ['x-agentcc-cost', Number(c.headers['x-agentcc-cost']).toFixed(6), c.expectedCost],
      ];
      for (const [name, got, want] of checks) if (got !== want) logIssues.push(`${c.label}: ${name} ${String(got)} ≠ ${String(want)}`);
    }
    addCase({ id: '4', title: 'Request Logs: one decision row per call, tokens and cost', ticket: 'One row per call, endpoint type decision, input tokens, cost at $0.042/M input and $0 output',
      verdict: logIssues.length ? 'FAIL' : 'PASS', actual: logIssues.join('; ') || 'every call has exactly one matching row', calls: [] });
    await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('typesafe/jev-latest').first()).toBeVisible({ timeout: UI_READY }).catch(() => undefined);
    await page.screenshot({ path: `${OUT_DIR}/02-request-logs.png`, fullPage: true });
  });

  // ---------------------------------------------------------------- Case 5
  await test.step('Case 5: key auth, provider allow-list, rate limit and budget', async () => {
    const body = { model: 'typesafe/jev-latest', state: TICKET, questions: Q.noul };
    const missing = await gw.post('/v1/systemone', { data: body });
    const invalid = await gw.post('/v1/systemone', { headers: { Authorization: 'Bearer sk-agentcc-not-a-real-key' }, data: body });
    addCase({ id: '5a', title: 'Missing or invalid gateway key', ticket: 'Key auth applies', calls: [],
      verdict: missing.status() === 401 && invalid.status() === 401 ? 'PASS' : 'FAIL',
      actual: `missing ${missing.status()}, invalid ${invalid.status()}` });

    const narrow = await owner.api.post<{ result: { key: string } }>('/agentcc/api-keys/',
      { name: `e2e-typesafe-noprov-${suffix}`, allowed_providers: ['openai'] });
    secrets.push(narrow.result.key);
    await expect.poll(async () => (await gw.get('/v1/decisions/models', { headers: { Authorization: `Bearer ${narrow.result.key}` } })).status(),
      CONFIG_VISIBLE).not.toBe(401);
    const b = await call('allowed_providers excludes typesafe', body, { headers: { Authorization: `Bearer ${narrow.result.key}` }, upstream: false });
    addCase({ id: '5b', title: 'Key whose allowed_providers excludes typesafe', ticket: 'refused (403)', calls: [b],
      verdict: b.status === 403 && !b.headers['x-agentcc-upstream-status'] ? 'PASS' : 'FAIL',
      actual: `${b.status} ${errorOf(b).code ?? ''}; upstream status ${b.headers['x-agentcc-upstream-status'] ?? 'none'}` });

    // agentcc_tokens_input_total summed over the typesafe series; undefined
    // when the gateway serves no /-/metrics (prometheus off in config.yaml).
    const metric = async () => {
      const res = await admin.get('/-/metrics');
      if (res.status() !== 200) return undefined;
      return (await res.text()).split('\n')
        .filter(l => l.startsWith('agentcc_tokens_input_total{') && l.includes(`provider="${PROVIDER}"`))
        .reduce((sum, l) => sum + Number(l.trim().split(/\s+/).pop()), 0);
    };
    const pushConfig = async (cfg: Record<string, unknown>, ready: (gatewayOrg: Record<string, unknown>) => boolean) => {
      await owner.api.post('/agentcc/org-configs/', { ...cfg, change_description: `e2e typesafe ${suffix}` });
      await expect.poll(async () => {
        const res = await admin.get('/-/orgs/configs');
        const org = (await res.json()).data?.[owner.organizationId] ?? {};
        return ready(org);
      }, { ...CONFIG_VISIBLE, message: 'org limits reach the gateway' }).toBe(true);
    };

    // (c) one request per minute per key.
    await pushConfig({ rate_limiting: { enabled: true, per_key_rpm: 1 } }, org => (org.rate_limiting as { per_key_rpm?: number })?.per_key_rpm === 1);
    const rl = await owner.api.post<{ result: { key: string } }>('/agentcc/api-keys/', { name: `e2e-typesafe-rpm-${suffix}` });
    secrets.push(rl.result.key);
    const rlAuth = { Authorization: `Bearer ${rl.result.key}` };
    await expect.poll(async () => (await gw.get('/v1/decisions/models', { headers: rlAuth })).status(), CONFIG_VISIBLE).not.toBe(401);
    const first = await call('rpm=1 first', body, { headers: rlAuth });
    const before = await metric();
    const second = await call('rpm=1 second', body, { headers: rlAuth, upstream: false });
    const after = await metric();
    second.logRow = await logRow(second.headers['x-agentcc-request-id'], false);
    addCase({ id: '5c', title: 'Org per_key_rpm=1: the second call is refused before the upstream', ticket: 'Rate limit applies; refused before the upstream call',
      calls: [first, second],
      verdict: first.status === 200 && second.status === 429 && !second.headers['x-agentcc-upstream-status'] && (before === undefined || before === after) ? 'PASS' : 'FAIL',
      actual: `first ${first.status}, second ${second.status} ${errorOf(second).code ?? ''}; upstream status ${second.headers['x-agentcc-upstream-status'] ?? 'none'}; `
        + `agentcc_tokens_input_total{provider="typesafe"} ${before === undefined ? 'n/a (/-/metrics disabled in this stack)' : `${before} → ${after}`}; log row ${JSON.stringify(second.logRow)}` });

    // (d) a key budget smaller than one call.
    const bk = await owner.api.post<{ result: { key: string; name: string } }>('/agentcc/api-keys/', { name: `e2e-typesafe-budget-${suffix}` });
    secrets.push(bk.result.key);
    await pushConfig({ rate_limiting: {}, budgets: { enabled: true, default_period: 'total', keys: { [bk.result.name]: { limit: 0.000001, hard: true } } } },
      org => !!(org.budgets as { keys?: Record<string, unknown> })?.keys?.[bk.result.name] && !(org.rate_limiting as { per_key_rpm?: number })?.per_key_rpm);
    const bkAuth = { Authorization: `Bearer ${bk.result.key}` };
    await expect.poll(async () => (await gw.get('/v1/decisions/models', { headers: bkAuth })).status(), CONFIG_VISIBLE).not.toBe(401);
    const spend = await call('budget first (spends past the limit)', body, { headers: bkAuth });
    const b1 = await metric();
    const refused = await call('budget second', body, { headers: bkAuth, upstream: false });
    const b2 = await metric();
    refused.logRow = await logRow(refused.headers['x-agentcc-request-id'], false);
    addCase({ id: '5d', title: 'Key budget: once spend crosses it the next call is refused before the upstream', ticket: 'Budget applies; refused before the upstream call',
      calls: [spend, refused],
      verdict: spend.status === 200 && refused.status >= 400 && refused.status < 500 && !refused.headers['x-agentcc-upstream-status'] && (b1 === undefined || b1 === b2) ? 'PASS' : 'FAIL',
      actual: `first ${spend.status} cost ${spend.headers['x-agentcc-cost']}; second ${refused.status} ${errorOf(refused).code ?? ''}; upstream status ${refused.headers['x-agentcc-upstream-status'] ?? 'none'}; `
        + `metric ${b1 === undefined ? 'n/a' : `${b1} → ${b2}`}; log row ${JSON.stringify(refused.logRow)}` });
    if (refused.status === 200) {
      report.specQuestions.push('A key budget set in the dashboard (org config budgets.keys) is not enforced when the gateway\'s config.yaml '
        + 'leaves budgets.enabled unset: the budget plugin is only added to the pipeline then (cmd/agentcc/main.go), and '
        + 'applyOrgBudgetOverrides only sets request metadata. Intended for self-hosted gateways, or should org budgets load the plugin?');
    }
    await pushConfig({ rate_limiting: {}, budgets: {} }, org => !(org.budgets as { enabled?: boolean })?.enabled);
  });

  // ---------------------------------------------------------------- Case 6
  await test.step('Case 6: error paths', async () => {
    // No provider key on the org path: can the dashboard API save one at all?
    const nokey = await tryApi(() => owner.api.post('/agentcc/provider-credentials/', {
      provider_name: 'typesafe-nokey', base_url: BASE_URL || 'https://api.typesafe.ai', api_format: 'systemone', models_list: ['jev-latest'],
    }));
    let nokeyCall: CallRecord | undefined;
    if (nokey.status >= 200 && nokey.status < 300) {
      await new Promise(r => setTimeout(r, 5_000));
      nokeyCall = await call('provider saved without a key', { model: 'typesafe-nokey/jev-latest', state: TICKET, questions: Q.noul }, { upstream: false });
      nokeyCall.logRow = await logRow(nokeyCall.headers['x-agentcc-request-id'], false);
    }
    addCase({ id: '6a', title: 'No provider key', ticket: 'No provider key: refused locally', calls: nokeyCall ? [nokeyCall] : [],
      verdict: nokeyCall ? (nokeyCall.status === 403 && !nokeyCall.headers['x-agentcc-upstream-status'] ? 'PASS' : 'FAIL') : 'NOTE',
      actual: nokeyCall ? `${nokeyCall.status} ${errorOf(nokeyCall).code}: ${errorOf(nokeyCall).message}; log row ${JSON.stringify(nokeyCall.logRow)}`
        : `the provider-credentials API refused a TypeSafe provider without a key (${nokey.status}), so the gateway path cannot be reached from the dashboard` });

    const malformed: Array<[string, unknown, string | undefined, number, string]> = [
      ['questions missing', { model: 'typesafe/jev-latest', state: TICKET }, undefined, 400, 'missing_questions'],
      ['question not an object', { model: 'typesafe/jev-latest', state: TICKET, questions: { q: 'yes?' } }, undefined, 400, 'invalid_question'],
      ['invalid JSON', undefined, '{"model":"typesafe/jev-latest",', 400, 'invalid_json'],
      ['questions as an array', { model: 'typesafe/jev-latest', state: TICKET, questions: [{ name: 'q', type: 'noul' }] }, undefined, 422, 'format_mismatch'],
    ];
    const envelopeCalls: CallRecord[] = [];
    const envelopeIssues: string[] = [];
    for (const [label, body, raw, status, code] of malformed) {
      const c = await call(label, body, { raw, upstream: false });
      c.logRow = await logRow(c.headers['x-agentcc-request-id'], false);
      envelopeCalls.push(c);
      if (c.status !== status || errorOf(c).code !== code || c.headers['x-agentcc-upstream-status']) {
        envelopeIssues.push(`${label}: ${c.status} ${errorOf(c).code} (want ${status} ${code})`);
      }
    }
    addCase({ id: '6b', title: 'Malformed questions (envelope)', ticket: 'Malformed questions: 400 with a clear message', calls: envelopeCalls,
      verdict: envelopeIssues.length ? 'FAIL' : 'PASS',
      actual: envelopeIssues.join('; ') || envelopeCalls.map(c => `${c.label}: ${c.status} ${errorOf(c).code} "${errorOf(c).message}" (log ${c.logRow === 'no row' ? 'no row' : 'row'})`).join('; ') });
    report.specQuestions.push('An array `questions` on /v1/systemone returns 422 format_mismatch, not 400; by design (decision_resolve.go errFormatMismatch).');

    const semantic: CallRecord[] = [];
    for (const [label, questions] of [
      ['choice without criteria', { q: { type: 'choice', instructions: 'Pick one.' } }],
      ['score with one level', { q: { type: 'score', instructions: 'Rate it.', criteria: ['only'] } }],
    ] as const) {
      const c = await call(label, { model: 'typesafe/jev-latest', state: TICKET, questions });
      c.logRow = await logRow(c.headers['x-agentcc-request-id'], c.status === 200 || !!c.headers['x-agentcc-upstream-status']);
      semantic.push(c);
    }
    const semOk = semantic.every(c => c.status === 400 || c.status === 422);
    addCase({ id: '6c', title: 'Semantically bad questions reach TypeSafe', ticket: 'Malformed questions: 400 with a clear message', calls: semantic,
      verdict: semOk ? (semantic.some(c => errorOf(c).message === 'TypeSafe rejected the request.') ? 'NOTE' : 'PASS') : 'FAIL',
      actual: semantic.map(c => `${c.label}: gateway ${c.status} ${errorOf(c).code}, upstream ${c.headers['x-agentcc-upstream-status'] ?? 'none'}, message "${errorOf(c).message}"`).join('; ') });

    const refusal = await call('200 refusal answer', { model: 'typesafe/jev-latest', state: `${TICKET} e2e-decision:refuse`, questions: Q.noul });
    const refusalAnswer = (refusal.body as { answers?: Record<string, { type?: string }> })?.answers?.resolved?.type;
    addCase({ id: '6d', title: 'A 200 answer holding a refusal passes through', ticket: 'Upstream refusal: passed through, not a 502', calls: [refusal],
      verdict: refusal.status === 200 ? (refusalAnswer === 'refusal' ? 'PASS' : 'NOTE') : 'FAIL',
      actual: refusalAnswer === 'refusal' ? '200 with answers.resolved = {"type":"refusal"}, unchanged'
        : `${refusal.status}; TypeSafe answered "${refusalAnswer}" (a live refusal cannot be forced with synthetic text; dry-run evidence covers it)` });

    const badKey = await call('provider with an invalid TypeSafe key', { model: `${BAD_KEY_PROVIDER}/jev-latest`, state: TICKET, questions: Q.noul });
    badKey.logRow = await logRow(badKey.headers['x-agentcc-request-id'], true);
    addCase({ id: '6e', title: 'Upstream rejects the key', ticket: 'Upstream refusal: passed through, not a 502', calls: [badKey],
      verdict: badKey.status === 403 && errorOf(badKey).code === 'provider_key_rejected' && ['401', '403'].includes(badKey.headers['x-agentcc-upstream-status'] ?? '') ? 'PASS' : 'FAIL',
      actual: `${badKey.status} ${errorOf(badKey).code}, upstream status ${badKey.headers['x-agentcc-upstream-status'] ?? 'none'}` });
  });

  // ---------------------------------------------------------------- Case 7
  await test.step('Case 7: pinned jev-1.14.0 vs jev-latest pricing', async () => {
    const body = (model: string, state = TICKET) => ({ model, state, questions: Q.noul });
    const asShipped = await call('jev-1.14.0 on the preset provider', body('typesafe/jev-1.14.0'), { upstream: false });
    asShipped.logRow = await logRow(asShipped.headers['x-agentcc-request-id'], false);
    const presetListsPinned = asShipped.status === 200;
    addCase({ id: '7a', title: 'typesafe/jev-1.14.0 on a preset-created provider', ticket: 'The pinned version and jev-latest give the same pricing',
      calls: [asShipped], verdict: presetListsPinned ? 'PASS' : 'FAIL',
      actual: `${asShipped.status} ${errorOf(asShipped).code ?? ''}: ${errorOf(asShipped).message ?? ''}` });
    if (!presetListsPinned) {
      report.bugDrafts.push({
        title: 'Dashboard TypeSafe preset omits jev-1.14.0, so typesafe/jev-1.14.0 is refused on a preset-created provider',
        repro: 'Gateway → Providers → Add Provider → TypeSafe (key) → curl -s localhost:28090/v1/systemone -H "Authorization: Bearer $GATEWAY_KEY" '
          + '-d \'{"model":"typesafe/jev-1.14.0","state":"…","questions":{"q":{"type":"noul"}}}\'',
        expected: 'The pinned version is usable and priced like jev-latest ($0.042/M input, $0 output).',
        actual: `${asShipped.status} ${errorOf(asShipped).code ?? ''}`,
        suspect: 'frontend/src/sections/gateway/providers/AddProviderDialog.jsx TypeSafe preset models ["jev-1.13.0","jev-latest"]',
        severity: 'P2',
      });
    }

    // Add jev-1.14.0 and an unknown version to the provider's model list.
    const models = Array.from(new Set([...((providerCfg.models_list as string[]) ?? []), 'jev-1.14.0', 'jev-0.0.0']));
    const update = await tryApi(() => owner.api.patch(`/agentcc/provider-credentials/${providerCfg.id as string}/`, { models_list: models }));
    await expect.poll(async () => {
      const org = (await (await admin.get('/-/orgs/configs')).json()).data?.[owner.organizationId] ?? {};
      return ((org.providers?.[PROVIDER]?.models ?? []) as string[]).includes('jev-1.14.0');
    }, { ...CONFIG_VISIBLE, message: 'jev-1.14.0 listed at the gateway' }).toBe(true).catch(() => undefined);

    const big = largeState(20_000);
    const latestBig = await call('jev-latest, 20k+ input tokens', body('typesafe/jev-latest', big));
    const pinnedBig = await call('jev-1.14.0, 20k+ input tokens', body('typesafe/jev-1.14.0', big));
    const issues: string[] = [`model list update ${update.status}`];
    for (const c of [latestBig, pinnedBig]) {
      c.logRow = await logRow(c.headers['x-agentcc-request-id'], c.status === 200);
      const usage = (c.body as { usage?: { input_tokens: number; output_tokens: number } }).usage;
      c.expectedCost = usage ? expectedCost(usage.input_tokens) : undefined;
      const got = Number(c.headers['x-agentcc-cost']).toFixed(6);
      if (c.status !== 200 || got !== c.expectedCost) issues.push(`${c.label}: ${c.status} cost ${got} want ${c.expectedCost} (${usage?.input_tokens} in, ${usage?.output_tokens} out)`);
      if (!DRY_RUN && (usage?.input_tokens ?? 0) < 20_000) issues.push(`${c.label}: only ${usage?.input_tokens} input tokens`);
      const row = c.logRow as Record<string, unknown>;
      if (row !== null && typeof row === 'object' && Number(row.cost).toFixed(6) !== c.expectedCost) issues.push(`${c.label}: log cost ${String(row.cost)}`);
    }
    const listing = await gw.get('/v1/decisions/models', { headers: auth });
    const entries = ((await listing.json()).data ?? []) as Array<{ id: string; pricing?: { input_per_mtok: number; output_per_mtok: number } }>;
    const priced = (id: string) => entries.find(e => e.id === id || e.id.endsWith(`/${id}`))?.pricing;
    for (const id of ['jev-latest', 'jev-1.14.0']) {
      const p = priced(id);
      if (!p || p.input_per_mtok !== 0.042 || p.output_per_mtok !== 0) issues.push(`/v1/decisions/models ${id}: ${JSON.stringify(p)}`);
    }
    const perToken = (c: CallRecord) => {
      const u = (c.body as { usage?: { input_tokens: number } }).usage;
      return u?.input_tokens ? Number(c.headers['x-agentcc-cost']) / u.input_tokens : NaN;
    };
    addCase({ id: '7b', title: 'jev-1.14.0 and jev-latest priced alike once listed', ticket: 'The pinned version (typesafe/jev-1.14.0) and jev-latest give the same pricing',
      calls: [latestBig, pinnedBig],
      verdict: issues.length === 1 ? (DRY_RUN ? 'NOTE' : 'PASS') : 'FAIL',
      actual: `${issues.join('; ')}; per-token jev-latest ${perToken(latestBig).toExponential(3)}, jev-1.14.0 ${perToken(pinnedBig).toExponential(3)}`
        + (DRY_RUN ? '; dry run: the mock reports 37 tokens, so 6-dp cost is 0.000002 and the $0.042/M rate is shown only in the live run' : '') });

    const resolved = (latestBig.body as { model?: string })?.model;
    addCase({ id: '7c', title: 'What TypeSafe returns as the model for jev-latest', ticket: 'The resolved model version is recorded',
      calls: [], verdict: 'NOTE',
      actual: `TypeSafe "model" for jev-latest: "${String(resolved)}"; Request Logs resolved_model ${JSON.stringify((latestBig.logRow as Record<string, unknown>)?.resolved_model)}; cost ${latestBig.headers['x-agentcc-cost']}` });

    const unknown = await call('unknown version jev-0.0.0', body('typesafe/jev-0.0.0'));
    unknown.logRow = await logRow(unknown.headers['x-agentcc-request-id'], !!unknown.headers['x-agentcc-upstream-status']);
    addCase({ id: '6f', title: 'Unknown model upstream', ticket: 'Upstream refusal: passed through, not a 502', calls: [unknown],
      verdict: unknown.status === 502 ? 'FAIL' : (unknown.status === 404 ? 'PASS' : 'NOTE'),
      actual: `${unknown.status} ${errorOf(unknown).code}, upstream status ${unknown.headers['x-agentcc-upstream-status'] ?? 'none'}`
        + (DRY_RUN ? ' (the mock answers 400 for a model it does not know; live TypeSafe is expected to answer 404 → model_not_found)' : '') });
  });
}

function writeReport() {
  report.header.upstreamCalls = Math.max(report.header.upstreamCalls, 0);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const json = redact(JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(OUT_DIR, 'report.json'), json);
  const lines: string[] = [
    `# TypeSafe Jev ${report.header.mode} report`, '',
    `- dev SHA: ${report.header.devSha}`, `- images: ${report.header.images}`, `- started: ${report.header.startedAt}`,
    `- TypeSafe endpoint: ${report.header.typesafeEndpoint}`,
    `- upstream calls: ${report.header.upstreamCalls}; upstream input tokens: ${report.header.upstreamInputTokens}`, '',
    '| Case | Title | Verdict | Ticket wording | Actual |', '| --- | --- | --- | --- | --- |',
    ...report.cases.map(c => `| ${c.id} | ${c.title} | ${c.verdict} | ${c.ticket} | ${c.actual.replace(/\|/g, '\\|')} |`), '',
    '## Calls', '',
  ];
  for (const c of report.cases) {
    for (const k of c.calls) {
      lines.push(`### ${c.id} — ${k.label}`, '', '```json', JSON.stringify({ request: k.request, status: k.status, headers: k.headers,
        body: k.body, request_logs_row: k.logRow, expected_cost: k.expectedCost ?? null }, null, 2), '```', '');
    }
  }
  lines.push('## Bug drafts', '', ...(report.bugDrafts.length ? report.bugDrafts.map(b =>
    `- **${b.title}** (${b.severity})\n  - repro: \`${b.repro}\`\n  - expected: ${b.expected}\n  - actual: ${b.actual}\n  - suspect: ${b.suspect}`) : ['none']), '',
  '## Spec questions for the owner', '', ...report.specQuestions.map(q => `- ${q}`), '');
  fs.writeFileSync(path.join(OUT_DIR, 'report.md'), redact(lines.join('\n')));
}
