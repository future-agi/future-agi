import type { Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { test, expect } from '../lib/scope-actors';
import type { ScopeActor } from '../lib/scope-actors';
import { ApiError } from '../lib/api-client';
import { E2E } from '../lib/env';
import {
  MOCK_REFUSAL_TEXT, TICKET, answerShape, countUpstream, expectedCost, largeCallIssues, largeState, refusalVerdict, rowIssues,
  semanticVerdict, unknownModelVerdict, rotateOutcome, gatewayHoldsKey,
} from '../lib/typesafe-live';
import type { CallRecord, Verdict } from '../lib/typesafe-live';

// Opt-in live run of TypeSafe's Jev decision models through the whole
// product: a TypeSafe provider added in the dashboard, a gateway key,
// POST /v1/systemone, Request Logs and cost. It calls TypeSafe's live API and
// spends real money, so it is skipped unless both are set:
//   E2E_TYPESAFE_LIVE=1  TYPESAFE_API_KEY=<a TypeSafe test key with a spend cap>
// TYPESAFE_BASE_URL overrides the upstream for a dry run against the stack's
// mock (http://mock-llm:8080 with TYPESAFE_API_KEY set to the mock's synthetic
// TypeSafe key, see e2e/stack/mock-llm/server.mjs), which also needs
// AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=true on the backend and the gateway. A
// dry run proves the harness, not TypeSafe.
//
// It writes test-results/typesafe-live/report.{json,md}: per case the request
// (a long state truncated), the response, the Request Logs row or "no row",
// the expected cost and a verdict. Findings are soft assertions, so every case
// runs and the report is complete; any FAIL fails the test.
//
// The TypeSafe key never reaches Playwright, so it cannot reach its reports
// either. (The synthetic gateway keys do: they come back in the bodies of
// owner.api.post('/agentcc/api-keys/'), which runs through Playwright's request
// context. They are local to this stack and deleted with it; with trace off
// they are not recorded.) Playwright titles an input step with
// the value typed (`Fill "<value>"`), snapshots a failing page's inputs
// (password fields included) into error-context.md, and stores an API call's
// log, request headers included, as the step's error on a transport failure;
// the HTML and blob reports keep all three. So the dashboard dialog gets a
// placeholder key, the real one is set through the provider's rotate API, and
// every call carrying a key goes through Node's fetch. Check a run's
// artefacts before sharing them:
//   SCAN_SECRET="$TYPESAFE_API_KEY" node scripts/scan-artefacts-for-secret.mjs playwright-report test-results
//
// Budget: about 40 upstream calls and 60k-85k input tokens per run (< $0.01);
// the header's totals count every call TypeSafe answered.

const LIVE = process.env.E2E_TYPESAFE_LIVE === '1';
const TYPESAFE_KEY = process.env.TYPESAFE_API_KEY ?? '';
const BASE_URL = process.env.TYPESAFE_BASE_URL ?? '';
// A dry run is any override that is not TypeSafe's own host; an unparsable one
// counts as a dry run rather than breaking test discovery while disabled.
const DRY_RUN = BASE_URL !== '' && (() => {
  try { return new URL(BASE_URL).hostname !== 'api.typesafe.ai'; } catch { return true; }
})();
// docker-compose.distributed.yml AGENTCC_ADMIN_TOKEN default; see provider-path-prefix.spec.ts.
const GATEWAY_ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
const OUT_DIR = fileURLToPath(new URL('../test-results/typesafe-live', import.meta.url));
const UI_READY = 60_000;
const LOG_VISIBLE = { timeout: 90_000, intervals: [2000, 5000] };
const CONFIG_VISIBLE = { timeout: 120_000, intervals: [2000, 5000] };
const PROVIDER = 'typesafe';
const BAD_KEY_PROVIDER = 'typesafe-badkey';

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
// Synthetic states only: made-up support tickets (TICKET) and lorem text.
const Q = {
  noul: { resolved: { type: 'noul', instructions: 'Was the customer promised a follow-up?' } },
  choice: { sentiment: { type: 'choice', instructions: 'What is the customer sentiment?',
    criteria: { positive: 'satisfied or thankful', neutral: 'matter of fact', negative: 'frustrated or upset' } } },
  score: { quality: { type: 'score', instructions: 'Rate the agent reply quality.',
    criteria: ['poor', 'fair', 'good', 'excellent'] } },
};
const ALL = { ...Q.noul, ...Q.choice, ...Q.score };

// Load-bearing, not just in case: no key is typed into the page (see above),
// but a trace records every request-context call with its response body, and
// the gateway keys come back in those bodies. Keep trace, screenshots and video
// off. scripts/typesafe-live-spec-guard.test.mjs fails if this line changes.
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

      try {
        await run(page, owner, suffix);
      } catch (e) {
        throw sanitized(e);
      } finally {
        writeReport();
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

// Playwright errors can carry a request's call log, headers included. Every
// error leaving a step is rethrown with the keys redacted.
function sanitized(e: unknown): Error {
  const err = e instanceof Error ? e : new Error(String(e));
  const clean = new Error(redact(err.message));
  clean.stack = redact(err.stack ?? err.message);
  return clean;
}
function step<T>(name: string, body: () => Promise<T>): Promise<T> {
  return test.step(name, async () => {
    try {
      return await body();
    } catch (e) {
      throw sanitized(e);
    }
  });
}

interface HttpResponse { status: number; headers: Record<string, string>; text: string }
// Node's fetch, not Playwright's request context: Playwright records each API
// call as a report step and, when the transport fails, keeps the call log with
// every request header (a gateway or TypeSafe key) as that step's error. An
// empty header value means "send no such header".
async function http(base: string, method: string, route: string,
  opts: { headers?: Record<string, string>; body?: string; timeoutMs?: number } = {}): Promise<HttpResponse> {
  const headers = Object.fromEntries(Object.entries(opts.headers ?? {}).filter(([, v]) => v !== ''));
  let res: Response;
  try {
    // Concatenated as ApiClient does, so a path-prefixed base (attach mode)
    // keeps its prefix; new URL(route, base) would drop it.
    res = await fetch(`${base.replace(/\/+$/, '')}${route}`, { method, headers, body: opts.body,
      signal: AbortSignal.timeout(opts.timeoutMs ?? 120_000) });
  } catch (e) {
    const err = e as Error & { cause?: { message?: string; code?: string } };
    throw new Error(redact(`${method} ${route}: ${err.message}${err.cause ? ` (${err.cause.code ?? err.cause.message ?? ''})` : ''}`));
  }
  return { status: res.status, headers: Object.fromEntries(res.headers), text: await res.text() };
}
const gateway = (method: string, route: string, headers: Record<string, string>, body?: string) =>
  http(E2E.gatewayUrl, method, route, { headers, body });
const admin = async (route: string) =>
  gateway('GET', route, { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` });
const json = (res: HttpResponse) => { try { return JSON.parse(res.text); } catch { return {}; } };

function addCase(c: CaseRecord) {
  report.cases.push(safe(c));
  expect.soft(c.verdict, `${c.id} ${c.title}: ${redact(c.actual)}`).not.toBe('FAIL');
}

async function run(page: Page, owner: ScopeActor, suffix: string) {
  const ownerHeaders = { Authorization: `Bearer ${owner.tokens.access}`, 'Content-Type': 'application/json',
    'X-Organization-Id': owner.organizationId, 'X-Workspace-Id': owner.workspaceId };
  // ---------------------------------------------------------------- Case 1
  const providerCfg = await step('Case 1: add the TypeSafe provider in the dashboard', async () => {
    await page.goto('/dashboard/gateway/providers', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Add Provider' }).first().click({ timeout: UI_READY });
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Provider').click();
    await page.getByRole('option', { name: 'TypeSafe', exact: true }).click();
    if (BASE_URL) await dialog.getByLabel('Base URL').fill(BASE_URL);
    // A placeholder, never the real key (see the header). System One
    // providers are never probed from the dialog, so it calls nothing.
    await dialog.getByLabel('API Key').fill(`ts-placeholder-${suffix}`);
    const saved = page.waitForResponse(r => r.url().includes('/agentcc/gateways/') && r.request().method() === 'POST',
      { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Add Provider' }).click();
    const savedRes = await saved;
    const savedStatus = savedRes.status();
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    await page.screenshot({ path: `${OUT_DIR}/01-providers.png`, fullPage: true });
    const creds = await owner.api.get<{ result?: unknown; results?: unknown }>('/agentcc/provider-credentials/');
    const list = (Array.isArray(creds) ? creds : (creds.results ?? creds.result ?? [])) as Array<Record<string, unknown>>;
    const typesafe = list.find(p => p.provider_name === PROVIDER);
    // The real key goes in through the rotate API, outside Playwright.
    const rotated = typesafe
      ? await http(E2E.apiUrl, 'POST', `/agentcc/provider-credentials/${String(typesafe.id)}/rotate/`,
        { headers: ownerHeaders, body: JSON.stringify({ credentials: { api_key: TYPESAFE_KEY } }) })
      : undefined;
    const rotate = rotateOutcome(rotated?.status, rotated ? json(rotated) : undefined);
    addCase({ id: '1a', title: 'Dashboard TypeSafe preset saves a provider; the key is set by rotate', ticket: 'Add a TypeSafe provider with a TypeSafe key',
      verdict: savedStatus === 200 && !!typesafe && rotate.ok ? 'PASS' : 'FAIL',
      actual: `save status ${savedStatus}; provider ${typesafe ? `saved, models ${JSON.stringify(typesafe.models_list)}` : 'missing'}; `
        + rotate.detail,
      calls: [] });
    // Every billed case after this needs the real key on the gateway. With
    // the placeholder they would all fail as if TypeSafe refused the key.
    if (typesafe && !rotate.ok) throw new Error(`TypeSafe key was not set (${rotate.detail}); cannot continue`);
    return typesafe ? { ...typesafe, saveUrl: savedRes.url() } as Record<string, unknown> & { saveUrl: string } : undefined;
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

  await step('gateway has the org providers and the key', async () => {
    await expect.poll(async () => {
      const res = await admin('/-/orgs/configs');
      if (res.status !== 200) return `status ${res.status}`;
      const configs = json(res);
      const providers = (configs.data?.[owner.organizationId]?.providers ?? {}) as Record<string, unknown>;
      // Booleans only: the response holds masked provider keys and is never
      // logged. The placeholder the dialog saved also makes the provider
      // present, so the (masked) real key must be what the gateway holds.
      return [PROVIDER, BAD_KEY_PROVIDER].every(p => p in providers)
        && gatewayHoldsKey(configs, owner.organizationId, PROVIDER, TYPESAFE_KEY) ? 'ready' : 'waiting';
    }, { ...CONFIG_VISIBLE, message: 'the org providers and the real TypeSafe key reach the gateway' }).toBe('ready');
    await expect.poll(async () => (await gateway('GET', '/v1/decisions/models', auth)).status,
      { ...CONFIG_VISIBLE, message: 'gateway key synced' }).toBe(200);
  });

  const call = async (label: string, body: unknown, opts: { headers?: Record<string, string>; raw?: string } = {})
    : Promise<CallRecord> => {
    const res = await gateway('POST', '/v1/systemone', { ...auth, ...(opts.headers ?? {}), 'Content-Type': 'application/json' },
      opts.raw ?? JSON.stringify(body));
    const text = res.text;
    let parsed: unknown = text;
    try { parsed = JSON.parse(text); } catch { /* keep text */ }
    const headers = Object.fromEntries(Object.entries(res.headers).filter(([k]) => k.startsWith('x-agentcc-')));
    const model = (body as { model?: string })?.model;
    const record: CallRecord = { label, request: { route: '/v1/systemone', model, body: truncateState(body ?? opts.raw) },
      status: res.status, headers, body: parsed, text, logRow: 'not checked' };
    countUpstream(report.header, record);
    return record;
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
  await step('Cases 2–4: question types, unchanged answer, resolved model, Request Logs', async () => {
    const shapes: Array<[string, Record<string, unknown>]> = [['noul', Q.noul], ['choice', Q.choice], ['score', Q.score], ['all three', ALL]];
    for (const [name, questions] of shapes) {
      const c = await call(`jev-latest ${name}`, { model: 'typesafe/jev-latest', state: `${TICKET} e2e-decision:pass`, questions });
      typed.push(c);
    }
    const answerTypes = typed.map(c => {
      const answers = (c.body as { answers?: Record<string, { type?: string }> })?.answers ?? {};
      return `${c.label}: ${c.status} ${Object.entries(answers).map(([k, v]) => `${k}=${v?.type}`).join(',')}`;
    });
    // Each call must answer exactly the questions it asked, each with its own type.
    const asked = shapes.map(([, questions]) => Object.fromEntries(
      Object.entries(questions).map(([id, q]) => [id, (q as { type: string }).type])));
    const typesOk = typed.every((c, i) => {
      const answers = (c.body as { answers?: Record<string, { type?: string }> })?.answers ?? {};
      const got = Object.fromEntries(Object.entries(answers).map(([id, a]) => [id, a?.type]));
      return c.status === 200 && isDeepStrictEqual(got, asked[i]);
    });

    const shape = answerShape(typed, { dryRun: DRY_RUN, provider: PROVIDER });
    addCase({ id: '3', title: 'Answer unchanged; resolved model recorded', ticket: 'The answer comes back unchanged, and the resolved model version is recorded',
      verdict: shape.fails.length ? 'FAIL' : shape.notes.length ? 'NOTE' : 'PASS',
      actual: [...shape.fails, ...shape.notes].join('; ')
        || `body keys answers,model,usage; TypeSafe model "${String((typed[0].body as Record<string, unknown>)?.model)}" = x-agentcc-model-used`, calls: [typed[0]] });

    const logIssues: string[] = [];
    for (const c of typed) {
      c.logRow = await logRow(c.headers['x-agentcc-request-id'], true);
      const usage = (c.body as { usage?: { input_tokens: number; output_tokens: number } }).usage;
      c.expectedCost = usage ? expectedCost(usage.input_tokens) : undefined;
      logIssues.push(...rowIssues(c, { model: 'typesafe/jev-latest', provider: PROVIDER }));
    }
    await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
    const uiRow = await page.getByText('typesafe/jev-latest').first().waitFor({ state: 'visible', timeout: UI_READY })
      .then(() => true, () => false);
    if (!uiRow) logIssues.push('Request Logs page shows no typesafe/jev-latest row');
    await page.screenshot({ path: `${OUT_DIR}/02-request-logs.png`, fullPage: true });
    // Recorded after the log lookups, so each call carries its row and expected cost.
    addCase({ id: '2', title: 'noul, choice, score and a combined call', ticket: 'Each question type: Noul, single choice, rubric score',
      verdict: typesOk ? 'PASS' : 'FAIL', actual: answerTypes.join('; '), calls: typed });
    addCase({ id: '4', title: 'Request Logs: one decision row per call, tokens and cost', ticket: 'One row per call, endpoint type decision, input tokens, cost at $0.042/M input and $0 output',
      verdict: logIssues.length ? 'FAIL' : 'PASS', actual: logIssues.join('; ') || 'every call has exactly one matching row; the row shows in Request Logs', calls: typed });
  });

  // ---------------------------------------------------------------- Case 5
  await step('Case 5: key auth, provider allow-list, rate limit and budget', async () => {
    const body = { model: 'typesafe/jev-latest', state: TICKET, questions: Q.noul };
    const missing = await call('no gateway key', body, { headers: { Authorization: '' } });
    const invalid = await call('invalid gateway key', body, { headers: { Authorization: 'Bearer sk-agentcc-not-a-real-key' } });
    addCase({ id: '5a', title: 'Missing or invalid gateway key', ticket: 'Key auth applies', calls: [missing, invalid],
      verdict: missing.status === 401 && invalid.status === 401 ? 'PASS' : 'FAIL',
      actual: `missing ${missing.status} ${errorOf(missing).code ?? ''}, invalid ${invalid.status} ${errorOf(invalid).code ?? ''}` });

    const narrow = await owner.api.post<{ result: { key: string } }>('/agentcc/api-keys/',
      { name: `e2e-typesafe-noprov-${suffix}`, allowed_providers: ['openai'] });
    secrets.push(narrow.result.key);
    await expect.poll(async () => (await gateway('GET', '/v1/decisions/models', { Authorization: `Bearer ${narrow.result.key}` })).status,
      CONFIG_VISIBLE).not.toBe(401);
    const b = await call('allowed_providers excludes typesafe', body, { headers: { Authorization: `Bearer ${narrow.result.key}` } });
    addCase({ id: '5b', title: 'Key whose allowed_providers excludes typesafe', ticket: 'refused (403)', calls: [b],
      verdict: b.status === 403 && !b.headers['x-agentcc-upstream-status'] ? 'PASS' : 'FAIL',
      actual: `${b.status} ${errorOf(b).code ?? ''}; upstream status ${b.headers['x-agentcc-upstream-status'] ?? 'none'}` });

    // agentcc_tokens_input_total summed over the typesafe series; undefined
    // when the gateway serves no /-/metrics (prometheus off in config.yaml).
    const metric = async () => {
      const res = await admin('/-/metrics');
      if (res.status !== 200) return undefined;
      return res.text.split('\n')
        .filter(l => l.startsWith('agentcc_tokens_input_total{') && l.includes(`provider="${PROVIDER}"`))
        .reduce((sum, l) => sum + Number(l.trim().split(/\s+/).pop()), 0);
    };
    const pushConfig = async (cfg: Record<string, unknown>, ready: (gatewayOrg: Record<string, unknown>) => boolean) => {
      await owner.api.post('/agentcc/org-configs/', { ...cfg, change_description: `e2e typesafe ${suffix}` });
      await expect.poll(async () => {
        const org = json(await admin('/-/orgs/configs')).data?.[owner.organizationId] ?? {};
        return ready(org);
      }, { ...CONFIG_VISIBLE, message: 'org limits reach the gateway' }).toBe(true);
    };

    try {
      // (c) one request per minute per key.
      await pushConfig({ rate_limiting: { enabled: true, per_key_rpm: 1 } }, org => (org.rate_limiting as { per_key_rpm?: number })?.per_key_rpm === 1);
      const rl = await owner.api.post<{ result: { key: string } }>('/agentcc/api-keys/', { name: `e2e-typesafe-rpm-${suffix}` });
      secrets.push(rl.result.key);
      const rlAuth = { Authorization: `Bearer ${rl.result.key}` };
      await expect.poll(async () => (await gateway('GET', '/v1/decisions/models', rlAuth)).status, CONFIG_VISIBLE).not.toBe(401);
      const first = await call('rpm=1 first', body, { headers: rlAuth });
      const before = await metric();
      const second = await call('rpm=1 second', body, { headers: rlAuth });
      const after = await metric();
      second.logRow = await logRow(second.headers['x-agentcc-request-id'], false);
      addCase({ id: '5c', title: 'Org per_key_rpm=1: the second call is refused before the upstream', ticket: 'Rate limit applies; refused before the upstream call',
        calls: [first, second],
        // Without /-/metrics the third proof is missing: NOTE, not PASS.
        verdict: first.status === 200 && second.status === 429 && !second.headers['x-agentcc-upstream-status'] && (before === undefined || before === after)
          ? (before === undefined ? 'NOTE' : 'PASS') : 'FAIL',
        actual: `first ${first.status}, second ${second.status} ${errorOf(second).code ?? ''}; upstream status ${second.headers['x-agentcc-upstream-status'] ?? 'none'}; `
          + `agentcc_tokens_input_total{provider="typesafe"} ${before === undefined ? 'n/a (/-/metrics disabled in this stack)' : `${before} → ${after}`}; log row ${JSON.stringify(second.logRow)}` });

      // (d) a key budget smaller than one call.
      const bk = await owner.api.post<{ result: { key: string; name: string } }>('/agentcc/api-keys/', { name: `e2e-typesafe-budget-${suffix}` });
      secrets.push(bk.result.key);
      await pushConfig({ rate_limiting: {}, budgets: { enabled: true, default_period: 'total', keys: { [bk.result.name]: { limit: 0.000001, hard: true } } } },
        org => !!(org.budgets as { keys?: Record<string, unknown> })?.keys?.[bk.result.name] && !(org.rate_limiting as { per_key_rpm?: number })?.per_key_rpm);
      const bkAuth = { Authorization: `Bearer ${bk.result.key}` };
      await expect.poll(async () => (await gateway('GET', '/v1/decisions/models', bkAuth)).status, CONFIG_VISIBLE).not.toBe(401);
      const spend = await call('budget first (spends past the limit)', body, { headers: bkAuth });
      const b1 = await metric();
      const refused = await call('budget second', body, { headers: bkAuth });
      const b2 = await metric();
      refused.logRow = await logRow(refused.headers['x-agentcc-request-id'], false);
      addCase({ id: '5d', title: 'Key budget: once spend crosses it the next call is refused before the upstream', ticket: 'Budget applies; refused before the upstream call',
        calls: [spend, refused],
        verdict: spend.status === 200 && refused.status === 429 && /budget/.test(errorOf(refused).code ?? '') && !refused.headers['x-agentcc-upstream-status'] && (b1 === undefined || b1 === b2)
          ? (b1 === undefined ? 'NOTE' : 'PASS') : 'FAIL',
        actual: `first ${spend.status} cost ${spend.headers['x-agentcc-cost']}; second ${refused.status} ${errorOf(refused).code ?? ''}; upstream status ${refused.headers['x-agentcc-upstream-status'] ?? 'none'}; `
          + `metric ${b1 === undefined ? 'n/a' : `${b1} → ${b2}`}; log row ${JSON.stringify(refused.logRow)}` });
      if (refused.status === 200) {
        report.specQuestions.push('A key budget set in the dashboard (org config budgets.keys) is not enforced when the gateway\'s config.yaml '
          + 'leaves budgets.enabled unset: the budget plugin is only added to the pipeline then (cmd/agentcc/main.go), and '
          + 'applyOrgBudgetOverrides only sets request metadata. Intended for self-hosted gateways, or should org budgets load the plugin?');
      }
    } finally {
      // Always lift the org limits, even when a step above failed.
      await pushConfig({ rate_limiting: {}, budgets: {} },
        org => !(org.budgets as { enabled?: boolean })?.enabled && !(org.rate_limiting as { per_key_rpm?: number })?.per_key_rpm)
        .catch(() => undefined);
    }
  });

  // ---------------------------------------------------------------- Case 6
  await step('Case 6: error paths', async () => {
    // No provider key: save a keyless TypeSafe provider on both paths, the
    // dashboard dialog's (update-provider, key omitted as the dialog does when
    // the field is blank) and the provider-credentials API (an empty key), so a
    // refusal is the keyless-provider rule rather than a missing field.
    const nokeyBase = BASE_URL || 'https://api.typesafe.ai';
    const viaDialog = await tryApi(() => owner.api.post(new URL(providerCfg.saveUrl).pathname,
      { name: 'typesafe-nokey', config: { base_url: nokeyBase, api_format: 'systemone', models: ['jev-latest'] } }));
    const viaApi = await tryApi(() => owner.api.post('/agentcc/provider-credentials/', {
      provider_name: 'typesafe-nokey', credentials: { api_key: '' }, base_url: nokeyBase, api_format: 'systemone', models_list: ['jev-latest'],
    }));
    const saves = [['dashboard save (update-provider)', viaDialog], ['provider-credentials API', viaApi]] as const;
    // The keyless rule names the key; DRF's own "This field is required." does not.
    const keyRule = (r: { status: number; body: unknown }) => r.status === 400 && /api[ _]key/i.test(JSON.stringify(r.body));
    let nokeyCall: CallRecord | undefined;
    if (saves.some(([, r]) => r.status >= 200 && r.status < 300)) {
      await new Promise(r => setTimeout(r, 5_000));
      nokeyCall = await call('provider saved without a key', { model: 'typesafe-nokey/jev-latest', state: TICKET, questions: Q.noul });
      nokeyCall.logRow = await logRow(nokeyCall.headers['x-agentcc-request-id'], false);
    }
    const saveText = saves.map(([name, r]) => `${name} ${r.status} ${JSON.stringify(r.body).slice(0, 160)}`).join('; ');
    addCase({ id: '6a', title: 'No provider key', ticket: 'No provider key: refused locally', calls: nokeyCall ? [nokeyCall] : [],
      verdict: nokeyCall ? (nokeyCall.status === 403 && !nokeyCall.headers['x-agentcc-upstream-status'] ? 'PASS' : 'FAIL')
        : saves.every(([, r]) => keyRule(r)) ? 'PASS' : 'NOTE',
      actual: nokeyCall ? `${saveText}; gateway ${nokeyCall.status} ${errorOf(nokeyCall).code}: ${errorOf(nokeyCall).message}; log row ${JSON.stringify(nokeyCall.logRow)}`
        : `refused at save time, no upstream call possible: ${saveText}` });

    const malformed: Array<[string, unknown, string | undefined, number, string]> = [
      ['questions missing', { model: 'typesafe/jev-latest', state: TICKET }, undefined, 400, 'missing_questions'],
      ['question not an object', { model: 'typesafe/jev-latest', state: TICKET, questions: { q: 'yes?' } }, undefined, 400, 'invalid_question'],
      ['invalid JSON', undefined, '{"model":"typesafe/jev-latest",', 400, 'invalid_json'],
      ['questions as an array', { model: 'typesafe/jev-latest', state: TICKET, questions: [{ name: 'q', type: 'noul' }] }, undefined, 422, 'format_mismatch'],
    ];
    const envelopeCalls: CallRecord[] = [];
    const envelopeIssues: string[] = [];
    for (const [label, body, raw, status, code] of malformed) {
      const c = await call(label, body, { raw });
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
    addCase({ id: '6c', title: 'Semantically bad questions reach TypeSafe', ticket: 'Malformed questions: 400 with a clear message', calls: semantic,
      verdict: semanticVerdict(semantic, { dryRun: DRY_RUN }),
      actual: semantic.map(c => `${c.label}: gateway ${c.status} ${errorOf(c).code}, upstream ${c.headers['x-agentcc-upstream-status'] ?? 'none'}, message "${errorOf(c).message}"`).join('; ') });
    for (const c of semantic.filter(x => x.status === 200)) {
      report.specQuestions.push(`TypeSafe answered "${c.label}" (200) rather than refusing it. Is that its intended reading of the question, or should the gateway validate it?`);
    }

    const refusal = await call('200 refusal answer', { model: 'typesafe/jev-latest', state: `${TICKET} e2e-decision:refuse`, questions: Q.noul });
    const refusalAnswer = (refusal.body as { answers?: Record<string, { type?: string }> })?.answers?.resolved?.type;
    const refusalVerdictValue = refusalVerdict(refusal, { dryRun: DRY_RUN });
    addCase({ id: '6d', title: 'A 200 answer holding a refusal passes through', ticket: 'Upstream refusal: passed through, not a 502', calls: [refusal],
      verdict: refusalVerdictValue,
      actual: DRY_RUN
        ? (refusalVerdictValue === 'PASS' ? `200 with the mock's refusal body byte for byte: ${MOCK_REFUSAL_TEXT}`
          : `${refusal.status} ${String(refusal.text)}; want 200 with the mock's bytes ${MOCK_REFUSAL_TEXT}`)
        : refusalAnswer === 'refusal' ? '200 with answers.resolved = {"type":"refusal"}'
          : `${refusal.status}; TypeSafe answered "${refusalAnswer}" (a live refusal cannot be forced with synthetic text; dry-run evidence covers it)` });

    const badKey = await call('provider with an invalid TypeSafe key', { model: `${BAD_KEY_PROVIDER}/jev-latest`, state: TICKET, questions: Q.noul });
    badKey.logRow = await logRow(badKey.headers['x-agentcc-request-id'], true);
    addCase({ id: '6e', title: 'Upstream rejects the key', ticket: 'Upstream refusal: passed through, not a 502', calls: [badKey],
      verdict: badKey.status === 403 && errorOf(badKey).code === 'provider_key_rejected' && ['401', '403'].includes(badKey.headers['x-agentcc-upstream-status'] ?? '') ? 'PASS' : 'FAIL',
      actual: `${badKey.status} ${errorOf(badKey).code}, upstream status ${badKey.headers['x-agentcc-upstream-status'] ?? 'none'}` });
  });

  // ---------------------------------------------------------------- Case 7
  await step('Case 7: pinned jev-1.14.0 vs jev-latest pricing', async () => {
    const body = (model: string, state = TICKET) => ({ model, state, questions: Q.noul });
    const asShipped = await call('jev-1.14.0 on the preset provider', body('typesafe/jev-1.14.0'));
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
      const org = json(await admin('/-/orgs/configs')).data?.[owner.organizationId] ?? {};
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
      issues.push(...largeCallIssues(c, { dryRun: DRY_RUN, provider: PROVIDER, minTokens: 20_000 }));
    }
    const listing = await gateway('GET', '/v1/decisions/models', auth);
    const entries = (json(listing).data ?? []) as Array<{ id: string; pricing?: { input_per_mtok: number; output_per_mtok: number } }>;
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
      verdict: unknownModelVerdict(unknown, { dryRun: DRY_RUN }),
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
