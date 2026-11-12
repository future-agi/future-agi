// W01/W02: read-only money probes and local gateway calls.
// Product pin 651ab5bb8ac3a6c54d82c59bdd5a1f1271049aec. Storage columns are
// evidenced by agentcc views/serializers and guarded by information_schema;
// no private ledger implementation is read. Expected prices live apart from seeds.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { expect, request, test, type APIRequestContext, type APIResponse } from '@playwright/test';
import { E2E } from '../env';
import type { StateProbe } from '../state-probe';
import type { VerdictRow, WalkReceipt } from './receipt';

const root = fileURLToPath(new URL('../../../', import.meta.url));

export const PATHS = {
  models: '/dashboard/gateway/models',
  logs: '/dashboard/gateway/logs',
  keys: '/dashboard/gateway/keys',
  billing: '/dashboard/settings/billing',
} as const;
export const API = {
  keys: '/agentcc/api-keys/',
  status: '/agentcc/managed-models/',
  catalog: '/agentcc/managed-models/catalog/',
  logs: '/agentcc/request-logs/',
  providers: '/agentcc/provider-credentials/',
  credits: '/agentcc/managed-credits/',
  entries: '/agentcc/managed-credits/entries/',
  statement: '/agentcc/managed-credits/statement/',
  checkout: '/agentcc/managed-credits/checkout/',
  settings: '/agentcc/managed-credits/settings/',
  updateConfig: '/agentcc/gateways/default/update-config/',
} as const;
export const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
export const MANAGED_CREDITS_ROUTE = /\/agentcc\/managed-credits\//;
export { exhaustedMessage } from './credit-message';
export const tokenOf = (value: string) => `sha256:${createHash('sha256').update(value).digest('hex')}`;
// price_attempt.go + micros.go: upstream once, then half-up scaleBps(10500).
// openrouter_snapshot.json prices; mock-llm/wire.mjs usage = 7 / 7.
export const EXACT_PRICING = {
  'openai/gpt-4o': { upstream: '88', billed: '92', markup: '4', header: '0.000092' },
  'anthropic/claude-fable-5.1': { upstream: '420', billed: '441', markup: '21', header: '0.000441' },
  'google/gemma-2-27b-it': { upstream: '9', billed: '9', markup: '0', header: '0.000009' },
} as const;
export function usdToMicros(value: unknown): string {
  if (typeof value !== 'number' && typeof value !== 'string') {
    throw new Error('money walk: missing USD amount');
  }
  if (typeof value === 'number' && (!Number.isFinite(value) || Math.abs(value) > 1_000_000)) {
    throw new Error('money walk: amount requires a lossless wire reader');
  }
  const match = /^(-?)(\d{1,24})(?:\.(\d{1,24}))?(?:[eE]([+-]?\d{1,2}))?$/.exec(String(value));
  if (!match) throw new Error('money walk: malformed USD amount');
  const fraction = match[3] ?? '';
  const shift = 6 + Number(match[4] ?? 0) - fraction.length;
  const coefficient = BigInt(match[2] + fraction);
  let amount: bigint;
  if (shift >= 0) amount = coefficient * 10n ** BigInt(shift);
  else {
    const divisor = 10n ** BigInt(-shift);
    if (coefficient % divisor !== 0n) throw new Error('money walk: fractional micro is not accepted');
    amount = coefficient / divisor;
  }
  return String(match[1] ? -amount : amount);
}
export interface Call {
  status: number;
  requestId: string;
  cost: string;
  provider: string;
  modelUsed: string;
  body: any;
  text: string;
  /** Streams only: the text the deltas carry, and the usage the stream reported. */
  streamed?: { text: string; prompt: number | null; completion: number | null };
}

/** OpenAI chat chunks: the deltas' text and the usage chunk. */
export function readChatStream(raw: string): NonNullable<Call['streamed']> {
  let text = '';
  let prompt: number | null = null;
  let completion: number | null = null;
  for (const line of raw.split('\n')) {
    if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
    try {
      const chunk = JSON.parse(line.slice(6));
      for (const choice of chunk.choices ?? []) text += choice.delta?.content ?? '';
      if (chunk.usage) {
        prompt = chunk.usage.prompt_tokens ?? prompt;
        completion = chunk.usage.completion_tokens ?? completion;
      }
    } catch {
      /* a keep-alive or comment line */
    }
  }
  return { text, prompt, completion };
}

/** Anthropic Messages events: text_delta text, message_start input and message_delta output tokens. */
export function readMessagesStream(raw: string): NonNullable<Call['streamed']> {
  let text = '';
  let prompt: number | null = null;
  let completion: number | null = null;
  for (const line of raw.split('\n')) {
    if (!line.startsWith('data: ')) continue;
    try {
      const ev = JSON.parse(line.slice(6));
      if (ev.type === 'message_start') prompt = ev.message?.usage?.input_tokens ?? prompt;
      if (ev.type === 'content_block_delta' && ev.delta?.type === 'text_delta') text += ev.delta.text ?? '';
      if (ev.type === 'message_delta' && ev.usage) completion = ev.usage.output_tokens ?? completion;
    } catch {
      /* ping */
    }
  }
  return { text, prompt, completion };
}

/** One gateway key's calls, on the local gateway only. */
export class KeyCaller {
  private constructor(
    private readonly ctx: APIRequestContext,
    private readonly key: string,
  ) {}

  static async open(key: string, baseURL = E2E.gatewayUrl): Promise<KeyCaller> {
    if (
      !['http:', 'https:'].includes(new URL(baseURL).protocol) ||
      new URL(baseURL).username ||
      new URL(baseURL).password ||
      !['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseURL).hostname)
    )
      throw new Error('KeyCaller calls a local gateway only');
    return new KeyCaller(await request.newContext({ baseURL, maxRedirects: 0 }), key);
  }

  private async read(res: APIResponse, stream?: 'chat' | 'messages'): Promise<Call> {
    const text = await res.text();
    let body: any = null;
    if (!stream || res.status() >= 400) {
      try {
        body = JSON.parse(text);
      } catch {
        body = null;
      }
    }
    const h = res.headers();
    return {
      status: res.status(),
      requestId: h['x-agentcc-request-id'] ?? '',
      cost: h['x-agentcc-cost'] ?? '',
      provider: h['x-agentcc-provider'] ?? '',
      modelUsed: h['x-agentcc-model-used'] ?? '',
      body,
      text,
      ...(stream && res.status() < 400
        ? { streamed: stream === 'chat' ? readChatStream(text) : readMessagesStream(text) }
        : {}),
    };
  }

  /** POST /v1/chat/completions; `models` is the caller's own fallback list (G26, body models[]). */
  async chat(model: string, prompt: string, o: { stream?: boolean; models?: string[] } = {}): Promise<Call> {
    const res = await this.ctx.post('/v1/chat/completions', {
      headers: { Authorization: `Bearer ${this.key}` },
      data: {
        model,
        messages: [{ role: 'user', content: prompt }],
        ...(o.stream ? { stream: true } : {}),
        ...(o.models ? { models: o.models } : {}),
      },
    });
    return this.read(res, o.stream ? 'chat' : undefined);
  }

  /** POST /v1/messages, using the native Messages envelope. */
  async messages(model: string, prompt: string, o: { stream?: boolean } = {}): Promise<Call> {
    const res = await this.ctx.post('/v1/messages', {
      headers: { Authorization: `Bearer ${this.key}`, 'anthropic-version': '2023-06-01' },
      data: {
        model,
        max_tokens: 64,
        messages: [{ role: 'user', content: prompt }],
        ...(o.stream ? { stream: true } : {}),
      },
    });
    return this.read(res, o.stream ? 'messages' : undefined);
  }

  /** POST /v1beta/models/<model>:generateContent (the native Google GenAI route), key in x-goog-api-key. */
  async generateContent(model: string, prompt: string): Promise<Call> {
    const res = await this.ctx.post(`/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
      headers: { 'x-goog-api-key': this.key },
      data: { contents: [{ role: 'user', parts: [{ text: prompt }] }] },
    });
    return this.read(res);
  }

  dispose(): Promise<void> {
    return this.ctx.dispose();
  }
}

/** The JSON body of a gateway refusal, for an exact comparison. */
export const refusal = (c: Call) => ({
  status: c.status,
  code: c.body?.error?.code,
  limit_source: c.body?.error?.metadata?.limit_source,
});

export interface ConfigVersion {
  version: number;
  is_active: boolean;
  enabled: unknown;
}
/** The org's live agentcc_org_config versions, oldest first. */
export const configVersions = (probe: StateProbe, org: string) =>
  probe.pg<ConfigVersion>(
    `SELECT version, is_active, managed_models->'enabled' AS enabled FROM agentcc_org_config
    WHERE organization_id = $1 AND NOT deleted ORDER BY version`,
    [org],
  );

export const ACCOUNT_COLUMNS = [
  'granted_total_micros',
  'settled_spend_micros',
  'version',
  'gateway_acked_version',
  'low_balance_threshold_micros',
  'autoreload_enabled',
  'autoreload_threshold_micros',
  'autoreload_amount_micros',
  'autoreload_disabled_reason',
  'autoreload_consent_version',
  'autoreload_consent_at',
  'updated_at',
  'gateway_push_state',
  'platform_spend_micros',
  'purchased_total_micros',
  'purchased_balance_micros',
  'promo_balance_micros',
  'commit_balance_micros',
  'debt_micros',
] as const;
export type AccountRow = Record<(typeof ACCOUNT_COLUMNS)[number], string | number | boolean | null>;
/** The org's usage_managedcreditaccount, every value as text (byte-comparable), or null. */
export async function accountRow(probe: StateProbe, org: string): Promise<AccountRow | null> {
  const cols = ACCOUNT_COLUMNS.map((c) => `${c}::text AS ${c}`).join(', ');
  const rows = await probe.pg<AccountRow>(`SELECT ${cols} FROM usage_managedcreditaccount WHERE organization_id = $1`, [
    org,
  ]);
  return rows[0] ?? null;
}

export const ENTRY_COLUMNS = [
  'kind',
  'amount_micros',
  'idempotency_key',
  'request_id',
  'model',
  'stripe_ref',
  'note',
  'markup_micros',
  'upstream_cost_micros',
  'usage_estimated',
  'occurred_at',
] as const;
export interface EntryRow {
  kind: string;
  amount_micros: string;
  idempotency_key: string;
  request_id: string;
  model: string;
  stripe_ref: string;
  note: string;
  markup_micros: string | null;
  upstream_cost_micros: string | null;
  usage_estimated: boolean;
  occurred_at: string;
}
export const entryRows = (probe: StateProbe, org: string) =>
  probe.pg<EntryRow>(
    `SELECT kind, amount_micros::text, idempotency_key, request_id, model, stripe_ref, note,
    markup_micros::text, upstream_cost_micros::text, usage_estimated, occurred_at::text
    FROM usage_managedcreditentry WHERE organization_id = $1 ORDER BY occurred_at, idempotency_key`,
    [org],
  );
export interface ConsentRow {
  organization_id: string;
  actor_id: string | null;
  actor_identifier: string;
  consent_version: string;
  accepted_at: string;
  threshold_micros: string;
  amount_micros: string;
}
export const consentRows = (probe: StateProbe, org: string) =>
  probe.pg<ConsentRow>(
    `SELECT organization_id::text, actor_id::text, actor_identifier, consent_version, accepted_at::text,
    threshold_micros::text, amount_micros::text FROM agentcc_auto_reload_consent
    WHERE organization_id = $1 ORDER BY accepted_at, id`,
    [org],
  );
/** Hard preflight: absent columns are a harness STOP, never a soft receipt. */
export async function moneyColumnsPreflight(probe: StateProbe): Promise<void> {
  const required = {
    usage_managedcreditaccount: [...ACCOUNT_COLUMNS, 'organization_id'],
    usage_managedcreditentry: [...ENTRY_COLUMNS, 'organization_id'],
    agentcc_auto_reload_consent: [
      'id',
      'organization_id',
      'actor_id',
      'actor_identifier',
      'consent_version',
      'accepted_at',
      'threshold_micros',
      'amount_micros',
    ],
  };
  for (const [table, columns] of Object.entries(required)) {
    const rows = await probe.pg<{ column_name: string }>(
      `SELECT column_name FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = $1`,
      [table],
    );
    const found = new Set(rows.map((row) => row.column_name));
    expect(columns.filter((column) => found.has(column)).sort(), `STOP: ${table} reader columns`).toEqual(
      [...columns].sort(),
    );
  }
}
export interface LogRow {
  id: string;
  request_id: string;
  model: string;
  status_code: number;
  billing_mode: string | null;
  cost: string;
  cost_upstream: string | null;
  cost_markup: string | null;
  input_tokens: number;
  output_tokens: number;
  is_stream: boolean;
  attempt_count: number;
  has_client_ip: boolean;
  error_message: string;
}
export const logRows = (probe: StateProbe, org: string) =>
  probe.pg<LogRow>(
    `SELECT id::text, request_id, model, status_code, billing_mode, cost::text, cost_upstream::text, cost_markup::text,
      input_tokens, output_tokens, is_stream, attempt_count, (metadata ? 'client_ip') AS has_client_ip, error_message
     FROM agentcc_request_log WHERE organization_id = $1 ORDER BY created_at, request_id`,
    [org],
  );

export interface AttemptRow {
  request_id: string;
  index: number;
  kind: string;
  outcome: string;
  provider: string;
}
export const attemptRows = (probe: StateProbe, org: string) =>
  probe.pg<AttemptRow>(
    `SELECT request_id, index, kind, outcome, provider FROM agentcc_request_attempt
     WHERE organization_id = $1 ORDER BY request_id, index`,
    [org],
  );

/** Whether a table exists (a head may not carry it). */
export async function tableExists(probe: StateProbe, table: string): Promise<boolean> {
  const rows = await probe.pg<{ n: string }>(
    `SELECT count(*)::text AS n FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = $1`,
    [table],
  );
  return rows[0]?.n === '1';
}

/** Everything a money walk reads of an org, for a byte comparison at start and end
 * (org B's isolation): its rows in every money table, as JSON text, sorted. The
 * credit account is read by accountRow (its ledger and settings columns), not
 * whole: the reconciler's own bookkeeping columns move on their own schedule. */
export async function moneySnapshot(probe: StateProbe, org: string): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = { usage_managedcreditaccount: await accountRow(probe, org) };
  for (const table of [
    'agentcc_org_config',
    'agentcc_api_key',
    'agentcc_provider_credential',
    'usage_managedcreditentry',
    'agentcc_auto_reload_consent',
    'agentcc_request_log',
    'agentcc_request_attempt',
    'usage_stripeeventlog',
  ]) {
    out[table] = (await tableExists(probe, table))
      ? (
          await probe.pg<{ j: string }>(
            `SELECT row_to_json(x)::text AS j FROM "${table}" x WHERE organization_id = $1 ORDER BY 1`,
            [org],
          )
        ).map((r) => r.j)
      : 'absent';
  }
  return out;
}

/** The whole stack's container logs since `since` (ISO), read where they are and
 * never written anywhere: the walk asserts what they do not contain. */
export function stackLogs(since: string): { text: string; bytes: number } {
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT;
  const text = execFileSync(`${root}bin/e2e`, ['logs', '--no-color', '--since', since], {
    env,
    encoding: 'utf8',
    timeout: 120_000,
    maxBuffer: 256 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  return { text, bytes: Buffer.byteLength(text) };
}

/** One backend assertion of a walk, recorded in its receipt as PASS or FAIL with
 * a digest of what it read (never the value). `fn` asserts and returns what it
 * read. A failure is soft: the test fails at its end with every FAIL listed (the
 * failing assertion's own message, under the check's name), and the walk goes on
 * to its remaining checks, so one run reports every failing check. Setup and
 * preconditions stay hard `expect`s outside checked(). */
export async function checked<T>(
  receipt: WalkReceipt,
  row: Pick<VerdictRow, 'feature' | 'lane' | 'where'>,
  fn: () => Promise<T> | T,
): Promise<T | undefined> {
  const before = test.info().errors.length;
  try {
    const value = await fn();
    const failed = test.info().errors.length > before;
    receipt.verdict({
      ...row,
      verdict: failed ? 'FAIL' : 'PASS',
      detail: {
        read:
          value === undefined
            ? null
            : tokenOf(JSON.stringify(value, (_key, item) => (typeof item === 'bigint' ? String(item) : item))),
      },
    });
    return value;
  } catch (error) {
    receipt.verdict({ ...row, verdict: 'FAIL', detail: { error: 'assertion failed' } });
    // A fallibility run (E2E_WALK_FIRST_FAIL=1) stops at the first failing check.
    if (process.env.E2E_WALK_FIRST_FAIL === '1') throw error;
    expect.soft(String((error as Error)?.message ?? error).slice(0, 4000), `FAIL ${row.feature}`).toBe('');
    return undefined;
  }
}

/** Poll `read` until it returns the same JSON twice `gapMs` apart (a settled
 * baseline: an async push of the org's own fixture has finished). */
export async function settled<T>(read: () => Promise<T>, { gapMs = 5_000, timeoutMs = 60_000 } = {}): Promise<T> {
  const end = Date.now() + timeoutMs;
  let last = JSON.stringify(await read());
  while (Date.now() < end) {
    await new Promise((r) => setTimeout(r, gapMs));
    const value = await read();
    const now = JSON.stringify(value);
    if (now === last) return value;
    last = now;
  }
  throw new Error(`state did not settle within ${timeoutMs} ms`);
}
