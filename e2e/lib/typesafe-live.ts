// Verdict helpers for harness/typesafe-live.spec.ts, kept free of Playwright
// so scripts/typesafe-live.test.mjs can check them without a stack.

export type Verdict = 'PASS' | 'FAIL' | 'NOTE';
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
// Dry run: the mock's output is known, so the key set is exact and the first
// call's body must be the mock's bytes (a re-marshalled body fails). Live: the
// gateway writes TypeSafe's bytes through, so it cannot tell a TypeSafe field
// from one the gateway added; extra keys are a note, the gateway's own names fail.
export function answerShape(calls: CallRecord[], opts: { dryRun: boolean; provider: string })
  : { fails: string[]; notes: string[] } {
  const fails: string[] = [];
  const notes: string[] = [];
  for (const c of calls) {
    const b = c.body as Record<string, unknown>;
    const keys = Object.keys(b !== null && typeof b === 'object' ? b : {});
    const missing = REQUIRED_KEYS.filter(k => !keys.includes(k));
    const extra = keys.filter(k => !REQUIRED_KEYS.includes(k)).sort();
    if (opts.dryRun) {
      if (missing.length || extra.length) fails.push(`${c.label}: body keys ${[...keys].sort().join(',')}`);
    } else {
      if (missing.length) fails.push(`${c.label}: body lacks ${missing.join(',')}`);
      const gatewayKeys = extra.filter(k => GATEWAY_KEY.test(k));
      if (gatewayKeys.length) fails.push(`${c.label}: gateway-named body keys ${gatewayKeys.join(',')}`);
      const vendorKeys = extra.filter(k => !GATEWAY_KEY.test(k));
      if (vendorKeys.length) notes.push(`${c.label}: TypeSafe body also carries ${vendorKeys.join(',')} (passed through; check TypeSafe's raw answer)`);
    }
    if (c.headers['x-agentcc-provider'] !== opts.provider) fails.push(`${c.label}: x-agentcc-provider ${c.headers['x-agentcc-provider']}`);
    if (c.headers['x-agentcc-model-used'] !== b?.model) fails.push(`${c.label}: x-agentcc-model-used ${c.headers['x-agentcc-model-used']} vs body model ${String(b?.model)}`);
  }
  if (opts.dryRun && calls[0] && calls[0].text !== MOCK_NOUL_PASS_TEXT) {
    fails.push(`noul body is not the mock's bytes: ${String(calls[0].text)} (want ${MOCK_NOUL_PASS_TEXT})`);
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
