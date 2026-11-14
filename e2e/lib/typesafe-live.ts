// Verdict helpers for harness/typesafe-live.spec.ts, kept free of Playwright
// so scripts/typesafe-live.test.mjs can check them without a stack.
import { isDeepStrictEqual } from 'node:util';

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

// internal/modeldb/decision_models.go: Jev input $0.042 per million tokens, output $0.
export const INPUT_PER_TOKEN = 0.042e-6;
export const expectedCost = (inputTokens: number) => (inputTokens * INPUT_PER_TOKEN).toFixed(6);

// Synthetic states only: made-up support tickets and lorem text.
export const TICKET = 'Synthetic support ticket #E2E-4471: the customer says the export button on the reports page '
  + 'does nothing after the latest update; the agent asked for a screenshot and promised a follow-up within a day.';
export function largeState(minTokens: number): string {
  const lorem = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore '
    + 'et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip. ';
  // ~4 characters per token; a margin keeps it above minTokens.
  return `${TICKET}\n${lorem.repeat(Math.ceil((minTokens * 4.4) / lorem.length))}`;
}

// e2e/stack/mock-llm/server.mjs: model echoed, 37 input tokens, pass → noul 0.9.
export const MOCK_NOUL_PASS = { model: 'jev-latest', usage: { input_tokens: 37, output_tokens: 0 },
  answers: { resolved: { type: 'noul', noul: 0.9 } } };

const usageOf = (c: CallRecord) => (c.body as { usage?: { input_tokens: number; output_tokens: number } } | null)?.usage;
const errorOf = (c: CallRecord) => ((c.body as { error?: { code?: string; message?: string } } | null)?.error ?? {});

// Adds a call to the header's upstream totals.
export function countUpstream(counters: Counters, c: CallRecord, upstream = true): void {
  if (upstream && c.status === 200) {
    counters.upstreamCalls += 1;
    counters.upstreamInputTokens += Number(usageOf(c)?.input_tokens ?? 0);
  } else if (c.headers['x-agentcc-upstream-status']) {
    counters.upstreamCalls += 1;
  }
}

// Case 3: the answer comes back unchanged. fails make the case FAIL, notes NOTE.
export function answerShape(calls: CallRecord[], opts: { dryRun: boolean; provider: string })
  : { fails: string[]; notes: string[] } {
  const fails: string[] = [];
  for (const c of calls) {
    const b = c.body as Record<string, unknown>;
    const keys = Object.keys(b ?? {}).sort().join(',');
    if (keys !== 'answers,model,usage') fails.push(`${c.label}: body keys ${keys}`);
    if (c.headers['x-agentcc-provider'] !== opts.provider) fails.push(`${c.label}: x-agentcc-provider ${c.headers['x-agentcc-provider']}`);
    if (c.headers['x-agentcc-model-used'] !== b?.model) fails.push(`${c.label}: x-agentcc-model-used ${c.headers['x-agentcc-model-used']} vs body model ${String(b?.model)}`);
  }
  if (opts.dryRun && calls[0] && !isDeepStrictEqual(calls[0].body, MOCK_NOUL_PASS)) {
    fails.push(`noul body differs from the mock's: ${JSON.stringify(calls[0].body)}`);
  }
  return { fails, notes: [] };
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

// Case 7b: a 20k+ token call priced at the decision rate.
export function largeCallIssues(c: CallRecord, opts: { dryRun: boolean; provider: string; minTokens: number }): string[] {
  const issues: string[] = [];
  const usage = usageOf(c);
  const got = Number(c.headers['x-agentcc-cost']).toFixed(6);
  if (c.status !== 200 || got !== c.expectedCost) issues.push(`${c.label}: ${c.status} cost ${got} want ${c.expectedCost} (${usage?.input_tokens} in, ${usage?.output_tokens} out)`);
  if (!opts.dryRun && (usage?.input_tokens ?? 0) < opts.minTokens) issues.push(`${c.label}: only ${usage?.input_tokens} input tokens`);
  const row = c.logRow as Record<string, unknown>;
  if (row !== null && typeof row === 'object' && Number(row.cost).toFixed(6) !== c.expectedCost) issues.push(`${c.label}: log cost ${String(row.cost)}`);
  return issues;
}

// Case 6c: semantically bad questions that pass the gateway's envelope checks.
export function semanticVerdict(calls: CallRecord[], _opts: { dryRun: boolean }): Verdict {
  const ok = calls.every(c => (c.status === 400 || c.status === 422) && !!c.headers['x-agentcc-upstream-status']);
  if (!ok) return 'FAIL';
  return calls.some(c => errorOf(c).message === 'TypeSafe rejected the request.') ? 'NOTE' : 'PASS';
}

// Case 6d: a 200 answer holding a refusal.
export function refusalVerdict(c: CallRecord, _opts: { dryRun: boolean }): Verdict {
  const answer = (c.body as { answers?: Record<string, { type?: string }> } | null)?.answers?.resolved?.type;
  if (c.status !== 200) return 'FAIL';
  return answer === 'refusal' ? 'PASS' : 'NOTE';
}

// Case 6f: a model TypeSafe does not know.
export function unknownModelVerdict(c: CallRecord, opts: { dryRun: boolean }): Verdict {
  if (opts.dryRun) return c.status === 502 ? 'FAIL' : 'NOTE';
  return c.status === 404 && errorOf(c).code === 'model_not_found' && c.headers['x-agentcc-upstream-status'] === '404' ? 'PASS' : 'FAIL';
}
