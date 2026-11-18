// Verdict helpers for harness/typesafe-live.spec.ts, kept free of Playwright
// so scripts/typesafe-live.test.mjs can check them without a stack.

export type Verdict = 'PASS' | 'FAIL' | 'NOTE';

export interface CleanupProvider { id: string; provider_name: string }
export interface CleanupKey { id: string; name: string; status: string; gateway_key_id: string }
export interface GatewayCleanupKey { id: string; name: string; status: string; metadata: { org_id?: string } }
export interface LimitSnapshot {
  // Immutable backend version: activate, do not recreate/normalize its JSON.
  backend: { id: string; rate_limiting?: unknown; budgets?: unknown };
  // Read separately: Go's omitempty and config_push transformations differ from Django.
  gateway: { rate_limiting?: unknown; budgets?: unknown };
}
export interface TypeSafeResources {
  organizationId: string;
  baselineProviderIds: Set<string>; baselineKeyIds: Set<string>; baselineGatewayKeyIds: Set<string>;
  providerNames: Set<string>; keyNames: Set<string>;
  limits?: LimitSnapshot;
}
export interface TeardownResult {
  verdict: Verdict;
  checks: Array<{ label: string; verdict: Verdict; detail: string }>;
}
export interface LifecycleResult { testVerdict: 'PASS' | 'FAIL'; teardown: TeardownResult }
export interface TypeSafeCleanupIO {
  listProviders(): Promise<CleanupProvider[]>;
  listKeys(): Promise<CleanupKey[]>;
  listGatewayKeys(): Promise<GatewayCleanupKey[]>;
  deleteProvider(id: string): Promise<void>;
  revokeKey(id: string): Promise<void>;
  revokeGatewayKey(id: string): Promise<void>;
  restoreLimits(snapshot: LimitSnapshot): Promise<void>;
  backendLimitsRestored(snapshot: LimitSnapshot): Promise<boolean>;
  gatewayLimitsRestored(snapshot: LimitSnapshot): Promise<boolean>;
  gatewayProvidersAbsent(names: string[]): Promise<boolean>;
  gatewayKeyRevoked(key: CleanupKey): Promise<boolean>;
  waitUntil(check: () => Promise<boolean>): Promise<void>;
}

// Snapshot before the first write. Exact intended names recover a create whose
// response was lost; baseline IDs prevent cleanup from touching older resources.
export function createTypeSafeResources(providers: CleanupProvider[], keys: CleanupKey[],
  baseline: { organizationId: string; gatewayKeys: GatewayCleanupKey[] }): TypeSafeResources {
  if (!baseline.organizationId) throw new Error('Cleanup requires an owned organization');
  const gatewayKeys = gatewayKeyInventory({ data: baseline.gatewayKeys });
  return { organizationId: baseline.organizationId,
    baselineProviderIds: new Set(providers.map(p => p.id)), baselineKeyIds: new Set(keys.map(k => k.id)),
    baselineGatewayKeyIds: new Set([...gatewayKeys.map(k => k.id), ...keys.map(k => k.gateway_key_id)]),
    providerNames: new Set(), keyNames: new Set() };
}

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

// auth_bridge.provision_key -> GatewayClient.create_key -> KeyStore.Create:
// the org is metadata.org_id, NOT a top-level field. ListKeys is unpaginated.
// Strip prefix/hash/other metadata immediately; never retain or report key material.
export function gatewayKeyInventory(response: unknown): GatewayCleanupKey[] {
  if (!object(response) || !Array.isArray(response.data)) throw new Error('Gateway key inventory malformed');
  const ids = new Set<string>();
  return response.data.map(row => {
    if (!object(row) || !['id', 'name', 'status'].every(f => typeof row[f] === 'string' && row[f] !== '')
      || !['active', 'revoked'].includes(row.status as string)
      || ('metadata' in row && (!object(row.metadata) || Object.values(row.metadata).some(v => typeof v !== 'string')))
      || ids.has(row.id as string)) throw new Error('Gateway key inventory malformed');
    ids.add(row.id as string);
    const org = (row.metadata as Record<string, string> | undefined)?.org_id;
    return { id: row.id as string, name: row.name as string, status: row.status as string,
      metadata: org === undefined ? {} : { org_id: org } };
  });
}

// Require a valid admin listing, not an HTTP error/empty parse. An absent org in
// a valid listing has no providers. Presence with a different mask still fails:
// removing the whole provider proves more than merely changing its key.
export function gatewayProvidersAbsent(configs: unknown, orgId: string, names: string[]): boolean {
  const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
  if (!object(configs) || !object(configs.data)) return false;
  if (!(orgId in configs.data)) return true;
  const org = configs.data[orgId];
  // tenant/config.go uses json:"providers,omitempty": deleting the last
  // provider legitimately removes the field, rather than returning {}.
  if (!object(org)) return false;
  if (!('providers' in org)) return true;
  return object(org.providers) && names.every(name => !(name in (org.providers as Record<string, unknown>)));
}

// All operations are independent and best-effort. No exception text, response
// body, credential or masked key is retained in these shareable results.
export async function teardownTypeSafe(resources: TypeSafeResources | undefined, io: TypeSafeCleanupIO): Promise<TeardownResult> {
  if (!resources) return { verdict: 'NOTE', checks: [{ label: 'resource snapshot', verdict: 'NOTE', detail: 'No mutations started.' }] };
  const checks: TeardownResult['checks'] = [];
  const attempt = async (label: string, action: () => Promise<void>) => {
    try {
      await action();
      checks.push({ label, verdict: 'PASS', detail: 'completed' });
    } catch {
      checks.push({ label, verdict: 'FAIL', detail: 'Operation or verification failed; response details withheld.' });
    }
  };
  let providers: CleanupProvider[] = [], keys: CleanupKey[] = [], gatewayKeys: GatewayCleanupKey[] = [];
  const ownedGatewayKeys = (rows: GatewayCleanupKey[]) => rows.filter(k => {
    if (resources.baselineGatewayKeyIds.has(k.id) || !resources.keyNames.has(k.name)) return false;
    // An exact intended name with missing ownership is ambiguous, not absence.
    if (!k.metadata.org_id) throw new Error('Gateway key ownership unavailable');
    return k.metadata.org_id === resources.organizationId;
  });
  const ownedBackendKeys = (rows: CleanupKey[]) => rows.filter(k => resources.keyNames.has(k.name)
    && !resources.baselineKeyIds.has(k.id) && !resources.baselineGatewayKeyIds.has(k.gateway_key_id));
  await attempt('provider inventory', async () => {
    providers = (await io.listProviders()).filter(p => resources.providerNames.has(p.provider_name) && !resources.baselineProviderIds.has(p.id));
  });
  await attempt('backend key inventory', async () => { keys = ownedBackendKeys(await io.listKeys()); });
  await attempt('gateway key inventory', async () => {
    gatewayKeys = ownedGatewayKeys(gatewayKeyInventory({ data: await io.listGatewayKeys() }));
  });
  for (const [i, provider] of providers.entries()) {
    await attempt(`delete run provider ${i + 1}`, () => io.deleteProvider(provider.id));
  }
  for (const [i, key] of keys.entries()) {
    if (key.status !== 'revoked') await attempt(`revoke run backend key ${i + 1}`, () => io.revokeKey(key.id));
  }
  for (const [i, key] of gatewayKeys.entries()) {
    if (key.status !== 'revoked') await attempt(`revoke run gateway key ${i + 1}`, () => io.revokeGatewayKey(key.id));
  }
  // Provider deletion pushes the current active org config. Restore only AFTER
  // those pushes; use the runner-owned IO, never the aborted body API/finally.
  if (resources.limits) await attempt('restore org limits', () => io.restoreLimits(resources.limits!));
  await attempt('backend provider removal', () => io.waitUntil(async () => {
    const remaining = await io.listProviders();
    return !remaining.some(p => resources.providerNames.has(p.provider_name) && !resources.baselineProviderIds.has(p.id));
  }));
  await attempt('backend key revocation', () => io.waitUntil(async () => {
    const remaining = await io.listKeys();
    return ownedBackendKeys(remaining).every(k => k.status === 'revoked');
  }));
  await attempt('gateway provider config removal', () => io.waitUntil(() => io.gatewayProvidersAbsent([...resources.providerNames])));
  await attempt('gateway key revocation', () => io.waitUntil(async () => {
    const remaining = ownedGatewayKeys(gatewayKeyInventory({ data: await io.listGatewayKeys() }));
    const revoked = await Promise.all(keys.map(key => io.gatewayKeyRevoked(key)));
    return remaining.every(k => k.status === 'revoked') && revoked.every(Boolean);
  }));
  if (resources.limits) {
    await attempt('backend org limits restored', () => io.waitUntil(() => io.backendLimitsRestored(resources.limits!)));
    await attempt('gateway org limits restored', () => io.waitUntil(() => io.gatewayLimitsRestored(resources.limits!)));
  }
  return { verdict: checks.some(c => c.verdict === 'FAIL') ? 'FAIL' : 'PASS', checks };
}

interface LifecycleOptions {
  run(): Promise<void>;
  close?(): Promise<void>;
  teardown(signal: AbortSignal): Promise<TeardownResult>;
  record(result: LifecycleResult): void | Promise<void>;
}

// A runner timeout does not settle the body promise. The Playwright fixture
// calls finish() in its own timeout slot, not in the body's finally block.
// Budget: drain body (130s), close browser (10s), cleanup (125s), report (5s).
export const TYPE_SAFE_FIXTURE_TIMEOUT = 280_000;
const phaseBudgets = { drain: 130_000, close: 10_000, cleanup: 125_000, record: 5_000 };

async function bounded<T>(ms: number, action: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error('TypeSafe lifecycle phase timed out'));
    }, ms);
  });
  try { return await Promise.race([Promise.resolve().then(() => action(controller.signal)), timeout]); }
  finally { clearTimeout(timer); controller.abort(); }
}

export class TypeSafeLifecycle {
  private readonly controller = new AbortController();
  readonly signal = this.controller.signal;
  private options?: LifecycleOptions;
  private body?: Promise<void>;
  private settled = false;
  private bodyFailed = false;

  constructor(private readonly budgets = phaseBudgets) {}

  // Gate both sides of API awaits. In-flight writes are drained, not abandoned;
  // no continuation can start another write once runner teardown has begun.
  async guard<T>(action: () => Promise<T>): Promise<T> {
    this.signal.throwIfAborted();
    const result = await action();
    this.signal.throwIfAborted();
    return result;
  }

  guardApi<T extends object>(api: T): T {
    return new Proxy(api, { get: (target, property) => {
      const value = Reflect.get(target, property);
      return typeof value === 'function'
        ? (...args: unknown[]) => this.guard(() => Reflect.apply(value, target, args)) : value;
    } });
  }

  async run(options: LifecycleOptions): Promise<void> {
    if (this.options) throw new Error('TypeSafe lifecycle body already registered');
    this.options = options; // Register cleanup/report before any resource creation.
    this.body = Promise.resolve().then(options.run);
    try { await this.body; }
    catch (error) {
      this.bodyFailed = true;
      // Once finish() starts, the runner already owns the timeout/interruption.
      // Do not append its cancellation fallout as a second test failure.
      if (!this.signal.aborted) throw error;
    }
    finally { this.settled = true; }
  }

  async finish(runnerFailed: boolean): Promise<void> {
    if (!this.options) return;
    const options = this.options;
    const testFailed = runnerFailed || this.bodyFailed || !this.settled;
    this.controller.abort(new Error('TypeSafe body cancelled for teardown'));
    const checks: TeardownResult['checks'] = [];
    let drained = false;
    try {
      // Body reads consume signal. Writes already sent (including dashboard
      // saves) must settle before closing their transports or taking inventory.
      // Polls can swallow aborts until their 120s deadline, hence a 130s drain.
      await bounded(this.budgets.drain, async () => { await this.body?.catch(() => undefined); });
      drained = true;
    } catch {
      checks.push({ label: 'body cancellation', verdict: 'FAIL',
        detail: 'Body did not settle; cleanup skipped to avoid racing pending writes.' });
    }
    if (options.close) {
      try { await bounded(this.budgets.close, options.close); }
      catch { checks.push({ label: 'browser close', verdict: 'FAIL', detail: 'Browser close failed; details withheld.' }); }
    }
    let teardown: TeardownResult = { verdict: 'FAIL', checks: [] };
    if (drained) {
      try { teardown = await bounded(this.budgets.cleanup, options.teardown); }
      catch { checks.push({ label: 'teardown', verdict: 'FAIL', detail: 'Cleanup failed or timed out; details withheld.' }); }
    }
    teardown = { verdict: checks.length ? 'FAIL' : teardown.verdict, checks: [...checks, ...teardown.checks] };
    let recordFailed = false;
    try {
      await bounded(this.budgets.record, async () => { await options.record({ testVerdict: testFailed ? 'FAIL' : 'PASS', teardown }); });
    } catch { recordFailed = true; }
    // The runner already owns hard/soft/timeout errors. Finalization must never
    // replace them or relabel a passing body when only cleanup/reporting failed.
    if (!testFailed && teardown.verdict === 'FAIL') throw new Error('TypeSafe teardown failed; see the independent teardown result');
    if (!testFailed && recordFailed) throw new Error('TypeSafe report could not be written');
  }
}

export function presetGap(models: string[]): string | undefined {
  return models.includes('jev-1.14.0') ? undefined
    : 'Preset gap: the dashboard TypeSafe preset does not list jev-1.14.0. The full live/dry run ends red by design at Case 7a until the preset is fixed; this is not an upstream-key or teardown failure.';
}

export type LogRow = Record<string, unknown> | 'no row' | 'not checked';
export interface CallRecord {
  label: string;
  request: { route: string; model?: string; body: unknown };
  status: number;
  headers: Record<string, string>;
  body: unknown;
  text?: string;
  logRow: LogRow;
  expectedCost?: string;
}
export interface Counters { upstreamCalls: number; upstreamInputTokens: number }

// internal/modeldb/decision_models.go: Jev input $0.042 per million tokens,
// output $0. The gateway's per-token rate is inputPerMTok / 1_000_000 computed
// at run time (4.2000000000000006e-08, not the literal 4.2e-08), and its cost
// is tokens × rate printed with %.6f; the same float here keeps the 6-dp
// string identical at token counts such as 250, where the two round apart.
export const INPUT_PER_MTOK = 0.042;
const inputPerToken = (perMTok: number) => perMTok / 1_000_000;
export const expectedCost = (inputTokens: number, outputTokens = 0) =>
  (inputTokens * inputPerToken(INPUT_PER_MTOK) + outputTokens * 0).toFixed(6);

// Synthetic states only: made-up support tickets and lorem text.
export const TICKET = 'Synthetic support ticket #E2E-4471: the customer says the export button on the reports page '
  + 'does nothing after the latest update; the agent asked for a screenshot and promised a follow-up within a day.';
export function largeState(minTokens: number): string {
  const lorem = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore '
    + 'et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip. ';
  // English runs at about 4 characters per token; Jev's tokenizer is unknown.
  // Sized at 5.5 characters per token, so a tokenizer up to 37% more efficient
  // than English still sees minTokens, while the product's own input check
  // (futureagi/ee/jev/mapping.py check_input_size: characters / 4 must not
  // exceed JEV_MAX_STATE_TOKENS, 30,000) still accepts it. Two such calls cost
  // under $0.004 at $0.042/M.
  const repeats = Math.ceil((minTokens * CHARS_PER_TOKEN - TICKET.length - 1) / lorem.length);
  const state = `${TICKET}\n${lorem.repeat(Math.max(0, repeats))}`;
  if (state.length / 4 > MAX_STATE_ESTIMATE_TOKENS) {
    throw new Error(`a ${minTokens}-token state would be over the product's Jev input limit`);
  }
  return state;
}
export const CHARS_PER_TOKEN = 5.5;
// futureagi/ee/jev/mapping.py: JEV_MAX_STATE_TOKENS (30,000) at 4 characters
// per token, less a margin, so the 20k-token state stays inside it.
export const MAX_STATE_ESTIMATE_TOKENS = 28_000;

// The real key reaches the gateway only through the provider's rotate API
// (the dialog saves a placeholder). A run whose rotate did not succeed, or
// whose config push did not reach the gateway, would send every billed case
// with the placeholder: it must stop instead.
export function rotateOutcome(status: number | undefined, body: unknown): { ok: boolean; detail: string } {
  const synced = (body as { result?: { gateway_synced?: boolean } } | undefined)?.result?.gateway_synced;
  return { ok: status === 200 && synced === true, detail: `key rotate ${status ?? 'not run'}, gateway_synced ${String(synced)}` };
}

// The gateway admin API masks provider keys as agentcc-gateway's
// redactAPIKey does (internal/server/admin_orgs.go): the first 8 and the last
// 4 characters, or "****" for a key of 12 characters or fewer.
export const redactedKey = (key: string) => (key.length <= 12 ? '****' : `${key.slice(0, 8)}****${key.slice(-4)}`);

// True when the gateway's org config holds this key for the provider (by its
// masked form), so the dashboard's placeholder ("ts-placeholder-<suffix>")
// does not count. Returns a boolean only, and the configs response is never
// logged or attached.
export function gatewayHoldsKey(configs: unknown, orgId: string, provider: string, key: string): boolean {
  const providers = (configs as { data?: Record<string, { providers?: Record<string, { api_key?: unknown }> }> })
    ?.data?.[orgId]?.providers;
  return !!key && providers?.[provider]?.api_key === redactedKey(key);
}

// The mock's exact bytes for one question "resolved" (e2e/stack/mock-llm/server.mjs:
// model echoed, then answers, then usage with 37 input tokens; JSON.stringify).
// The gateway forwards a decision body byte for byte, so a dry run compares text.
export const mockDecisionText = (answer: Record<string, unknown>) =>
  JSON.stringify({ model: 'jev-latest', answers: { resolved: answer }, usage: { input_tokens: 37, output_tokens: 0 } });
export const MOCK_NOUL_PASS_TEXT = mockDecisionText({ type: 'noul', noul: 0.9 });
export const MOCK_REFUSAL_TEXT = mockDecisionText({ type: 'refusal' });
// Oracle for the pass-marker requests in Cases 2–3 and lifecycle mode only.
// Derived from stack/mock-llm/server.mjs, not from the received answer. The
// selftests compare this with the real mock handler; no vendor answer is guessed.
function mockPassText(c: CallRecord): string | undefined {
  const questions = (c.request.body as { questions?: Record<string, { type?: string; criteria?: unknown }> } | null)?.questions;
  if (!questions || typeof questions !== 'object' || Array.isArray(questions) || !Object.keys(questions).length) return undefined;
  const answers: Record<string, unknown> = {};
  for (const [id, q] of Object.entries(questions)) {
    if (q?.type === 'noul') answers[id] = { type: 'noul', noul: 0.9 };
    else if (q?.type === 'choice' || q?.type === 'score') {
      const score = q.type === 'score';
      const criteria = q.criteria;
      if (!criteria || typeof criteria !== 'object' || (score && !Array.isArray(criteria))) return undefined;
      const labels = score ? (criteria as unknown[]).map((_, i) => String(i)) : Object.keys(criteria);
      if (!labels.length) return undefined;
      const chosen = labels.length - 1;
      answers[id] = { type: q.type, confidence: 1,
        ...(score ? { score: chosen, legend: Object.fromEntries((criteria as unknown[]).map((v, i) => [String(i), v])) }
          : { choice: labels[chosen] }),
        probabilities: Object.fromEntries(labels.map((label, i) => [label, i === chosen ? 1 : 0])) };
    } else return undefined;
  }
  return JSON.stringify({ model: 'jev-latest', answers, usage: { input_tokens: 37, output_tokens: 0 } });
}
const REQUIRED_KEYS = ['answers', 'model', 'usage'];
// Body keys only the gateway would add: its own x-agentcc / agentcc names.
const GATEWAY_KEY = /^(x[-_])?agentcc/i;

const usageOf = (c: CallRecord) => (c.body as { usage?: { input_tokens: number; output_tokens: number } } | null)?.usage;
const errorOf = (c: CallRecord) => ((c.body as { error?: { code?: string; message?: string } } | null)?.error ?? {});

// Adds a call to the header's upstream totals (the live budget is <= 100 calls,
// <= 2M tokens). A 200 always means TypeSafe answered and billed it, whatever
// the caller expected; an error counts when the gateway reports an upstream status.
export function countUpstream(counters: Counters, c: CallRecord): void {
  if (c.status === 200) {
    counters.upstreamCalls += 1;
    counters.upstreamInputTokens += Number(usageOf(c)?.input_tokens ?? 0);
  } else if (c.headers['x-agentcc-upstream-status']) {
    counters.upstreamCalls += 1;
  }
}

// Case 3: the answer comes back unchanged. fails make the case FAIL, notes NOTE.
// Dry run: EVERY call must match the mock's bytes, including values, nested
// fields and serialization for each question in the combined call. Live: this
// only checks the envelope and headers, not equality to unobserved upstream
// bytes. Record that missing evidence as NOTE, never an unchanged-answer PASS.
export function answerShape(calls: CallRecord[], opts: { dryRun: boolean; provider: string })
  : { fails: string[]; notes: string[] } {
  const fails: string[] = [];
  const notes: string[] = [];
  if (!calls.length) fails.push('no calls to check for unchanged answers');
  for (const c of calls) {
    if (c.status !== 200) fails.push(`${c.label}: status ${c.status} (want 200)`);
    const b = c.body as Record<string, unknown>;
    const keys = Object.keys(b !== null && typeof b === 'object' ? b : {});
    const missing = REQUIRED_KEYS.filter(k => !keys.includes(k));
    const extra = keys.filter(k => !REQUIRED_KEYS.includes(k)).sort();
    if (opts.dryRun) {
      if (missing.length || extra.length) fails.push(`${c.label}: body keys ${[...keys].sort().join(',')}`);
      const expected = mockPassText(c);
      if (expected === undefined) fails.push(`${c.label}: no mock pass oracle for the requested questions`);
      else if (c.text !== expected) fails.push(`${c.label}: body is not the mock's bytes: ${String(c.text)} (want ${expected})`);
    } else {
      notes.push(`${c.label}: envelope and model headers checked; unchanged live answer NOT PROVEN without raw upstream bytes`);
      if (missing.length) fails.push(`${c.label}: body lacks ${missing.join(',')}`);
      const gatewayKeys = extra.filter(k => GATEWAY_KEY.test(k));
      if (gatewayKeys.length) fails.push(`${c.label}: gateway-named body keys ${gatewayKeys.join(',')}`);
      const vendorKeys = extra.filter(k => !GATEWAY_KEY.test(k));
      if (vendorKeys.length) notes.push(`${c.label}: TypeSafe body also carries ${vendorKeys.join(',')} (passed through; check TypeSafe's raw answer)`);
    }
    if (c.headers['x-agentcc-provider'] !== opts.provider) fails.push(`${c.label}: x-agentcc-provider ${c.headers['x-agentcc-provider']}`);
    if (c.headers['x-agentcc-model-used'] !== b?.model) fails.push(`${c.label}: x-agentcc-model-used ${c.headers['x-agentcc-model-used']} vs body model ${String(b?.model)}`);
  }
  return { fails, notes };
}

// Cases 4 and 7b: the call's Request Logs row against its response.
export function rowIssues(c: CallRecord, want: { model: string; provider: string }): string[] {
  if (c.logRow === 'no row') return [`${c.label}: no row`];
  if (c.logRow === 'not checked') return [`${c.label}: row not checked`];
  const r = c.logRow;
  const usage = usageOf(c);
  const checks: Array<[string, unknown, unknown]> = [
    ['rows', r.count, 1], ['model', r.model, want.model], ['provider', r.provider, want.provider],
    ['metadata.endpoint_type', r.endpoint_type, 'decision'], ['input_tokens', r.input_tokens, usage?.input_tokens],
    ['output_tokens', r.output_tokens, usage?.output_tokens], ['resolved_model', r.resolved_model, (c.body as Record<string, unknown>)?.model],
    ['cost', Number(r.cost).toFixed(6), c.expectedCost], ['x-agentcc-cost', Number(c.headers['x-agentcc-cost']).toFixed(6), c.expectedCost],
  ];
  return checks.filter(([, got, wantValue]) => got !== wantValue)
    .map(([name, got, wantValue]) => `${c.label}: ${name} ${String(got)} ≠ ${String(wantValue)}`);
}

// Case 7b: a 20k+ token call priced at the decision rate. These are the only
// calls large enough to show the rate at 6 dp, so a 200 needs its Request Logs
// row, held to Case 4's checks.
export function largeCallIssues(c: CallRecord, opts: { dryRun: boolean; provider: string; minTokens: number }): string[] {
  const issues: string[] = [];
  const usage = usageOf(c);
  const got = Number(c.headers['x-agentcc-cost']).toFixed(6);
  if (c.status !== 200 || got !== c.expectedCost) issues.push(`${c.label}: ${c.status} cost ${got} want ${c.expectedCost} (${usage?.input_tokens} in, ${usage?.output_tokens} out)`);
  if (!opts.dryRun && (usage?.input_tokens ?? 0) < opts.minTokens) issues.push(`${c.label}: only ${usage?.input_tokens} input tokens`);
  if (c.status === 200) issues.push(...rowIssues(c, { model: c.request.model ?? '', provider: opts.provider }));
  return issues;
}

// Case 6c: semantically bad questions that pass the gateway's envelope checks.
// TypeSafe refusing them must come back as a 4xx passed through. Live, TypeSafe
// answering one (200) is its call, recorded as a note and a spec question; only
// a 5xx or a refusal the gateway made without asking TypeSafe fails.
export function semanticVerdict(calls: CallRecord[], opts: { dryRun: boolean }): Verdict {
  const refused = (c: CallRecord) => (c.status === 400 || c.status === 422) && !!c.headers['x-agentcc-upstream-status'];
  if (!calls.every(c => refused(c) || (!opts.dryRun && c.status === 200))) return 'FAIL';
  if (calls.some(c => c.status === 200)) return 'NOTE';
  return calls.some(c => errorOf(c).message === 'TypeSafe rejected the request.') ? 'NOTE' : 'PASS';
}

// Case 6d: a 200 answer holding a refusal. The dry run forces one (the mock
// always refuses "e2e-decision:refuse"), so anything but the mock's bytes means
// the gateway changed the body. Live, a refusal cannot be forced: another
// answer is a note.
export function refusalVerdict(c: CallRecord, opts: { dryRun: boolean }): Verdict {
  if (c.status !== 200) return 'FAIL';
  if (opts.dryRun) return c.text === MOCK_REFUSAL_TEXT ? 'PASS' : 'FAIL';
  const answer = (c.body as { answers?: Record<string, { type?: string }> } | null)?.answers?.resolved?.type;
  return answer === 'refusal' ? 'PASS' : 'NOTE';
}

// Case 6f: a model TypeSafe does not know. The ticket asks for the refusal to
// be passed through, not turned into a 502. Live, 404 model_not_found is the
// prediction: it passes, another passed-through 4xx is recorded as a note.
export const MOCK_UNKNOWN_MODEL_MESSAGE = 'invalid synthetic decision request';
export function unknownModelVerdict(c: CallRecord, opts: { dryRun: boolean }): Verdict {
  if (c.status >= 500) return 'FAIL';
  // The mock refuses an unknown model with this 400 (e2e/stack/mock-llm), and
  // the gateway passes it through with the upstream status: anything else in
  // a dry run (a 200, or a local refusal with no upstream status) is a FAIL.
  if (opts.dryRun) {
    return c.status === 400 && c.headers['x-agentcc-upstream-status'] === '400'
      && errorOf(c).message === MOCK_UNKNOWN_MODEL_MESSAGE ? 'PASS' : 'FAIL';
  }
  if (c.status === 404 && errorOf(c).code === 'model_not_found' && c.headers['x-agentcc-upstream-status'] === '404') return 'PASS';
  return c.status >= 400 && c.headers['x-agentcc-upstream-status'] ? 'NOTE' : 'FAIL';
}
