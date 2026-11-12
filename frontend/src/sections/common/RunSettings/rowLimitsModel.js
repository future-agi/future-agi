// W6-16 (A1): limits per row on a prompt or agent run. Pure; the wizard's
// RowLimits field, the create body (cardExtras) and the mock run engine
// (sources/limits.js) read the same rules, so the screen and the run agree.
//
//   row_limits: {tokens, messages, cost_usd, timeout_s}   (each optional)
//   tokens     prompt + completion tokens summed over the try's steps; the
//              step that crosses the limit stops at it (its share up to the
//              limit is kept: `fraction`), nothing after it runs
//   messages   model and tool turns; the try keeps the first N
//   cost_usd   the summed cost of the try's steps; the crossing step stops
//              at the limit the same way
//   timeout_s  the run's existing time limit per try (limits.timeout_s):
//              one field, one meaning
// A value is never changed on its way to the run: a blank is no
// limit; anything else that is not a valid limit is an issue that blocks the
// Configuration step (rowLimitStepIssues), the Try and the create call
// (sources/limits.js `create`), so the run always uses the number shown.
import { fmtCost } from "src/sections/common/format/fmt";

/** The four limits in the order the field shows them. */
export const ROW_LIMIT_FIELDS = Object.freeze([
  { key: "tokens", label: "tokens", step: 1, integer: true },
  { key: "messages", label: "messages", step: 1, integer: true },
  { key: "cost_usd", label: "$", step: 0.01, integer: false, prefix: true },
  { key: "timeout_s", label: "s", step: 1, integer: true },
]);

/**
 * Limits this release refuses, on the screen and on the server
 * (experiment_scope_gate.UNAVAILABLE_ROW_LIMITS): their budget is not applied
 * to the model call yet. Their fields stay on the card; a value in one blocks
 * the Configuration step with UNAVAILABLE_ROW_LIMITS_TEXT. Empty this list to
 * accept them again.
 */
export const UNAVAILABLE_ROW_LIMITS = Object.freeze([
  "tokens",
  "messages",
  "cost_usd",
]);
export const UNAVAILABLE_ROW_LIMITS_TEXT =
  "Token, message and cost limits aren't available in this release. Clear them to run.";

/** How the time limit acts: checked before and after each call, not during one. */
export const TIME_LIMIT_HELP =
  "Once the time limit has passed, the row stops after the call in flight ends: the limit is checked before and after each model call or agent step, not during one.";

/**
 * What a time limit changes when a model call fails: the prompt row is not
 * run again (provider_policy.prompt_response turns the error into one the
 * row's retry policy does not retry). Without a time limit the row is run
 * again after a temporary failure.
 */
export const TIME_LIMIT_NO_RETRY =
  "In a prompt run with a time limit, a row whose model call fails is not run again, even after a temporary failure such as a rate limit.";

const isSet = (v) => v != null && v !== "";

/** UNAVAILABLE_ROW_LIMITS_TEXT when a card sets a refused limit, else null. */
export function unavailableRowLimitsText(raw) {
  return UNAVAILABLE_ROW_LIMITS.some((key) => isSet(raw?.[key]))
    ? UNAVAILABLE_ROW_LIMITS_TEXT
    : null;
}

const positive = (v) => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
};

const KEYS = new Set(ROW_LIMIT_FIELDS.map((f) => f.key));

/**
 * The valid limits of a row_limits value ({} when none). A value that is
 * not a valid limit is never rounded or guessed: it is left out here and
 * reported by rowLimitIssues, which blocks every path that would run it.
 */
export function normalizeRowLimits(raw) {
  const out = {};
  ROW_LIMIT_FIELDS.forEach(({ key, integer }) => {
    const n = positive(raw?.[key]);
    if (n != null && (!integer || Number.isInteger(n))) out[key] = n;
  });
  return out;
}

/** Field problems, one sentence each (`Tokens must be a whole number`). */
export function rowLimitIssues(raw) {
  const issues = [];
  ROW_LIMIT_FIELDS.forEach(({ key, integer }) => {
    const v = raw?.[key];
    if (v == null || v === "") return;
    const n = typeof v === "boolean" ? NaN : Number(v);
    const name =
      key === "cost_usd" ? "Cost" : key === "timeout_s" ? "Time" : key;
    const Name = name.charAt(0).toUpperCase() + name.slice(1);
    if (!Number.isFinite(n) || n <= 0) issues.push(`${Name} must be above 0`);
    else if (integer && !Number.isInteger(n))
      issues.push(`${Name} must be a whole number`);
  });
  return issues;
}

/**
 * The create body's problems with `row_limits` (the API answers 400 with
 * them): not an object, a key that is no limit, or rowLimitIssues.
 */
export function rowLimitsBodyIssues(raw) {
  if (raw == null) return [];
  if (typeof raw !== "object" || Array.isArray(raw))
    return [
      "row_limits must be an object of tokens, messages, cost_usd, timeout_s",
    ];
  const unknown = Object.keys(raw).filter((k) => !KEYS.has(k));
  return [
    ...unknown.map(
      (k) => `${k} is not a limit (tokens, messages, cost_usd, timeout_s)`,
    ),
    ...rowLimitIssues(raw),
  ];
}

/**
 * The Configuration step's issues of a card's limits (they block Continue
 * and Run, like every step issue): `Limits per row: Tokens must be a whole
 * number`, then UNAVAILABLE_ROW_LIMITS_TEXT when a refused limit is set.
 * [] for a card without limits or with only a time limit.
 */
export function rowLimitStepIssues(card) {
  const raw = card?.rowLimits ?? {};
  const unavailable = unavailableRowLimitsText(raw);
  return [
    ...rowLimitIssues(raw).map((t) => `Limits per row: ${t}`),
    ...(unavailable ? [unavailable] : []),
  ];
}

/** `Stopped at 2,000 tokens` · `Stopped at 12 messages` · `Stopped at $0.05 for this row` · `Timed out after 120 s`. */
export function stopText(stop) {
  if (!stop) return null;
  const at = Number(stop.at);
  if (stop.kind === "tokens")
    return `Stopped at ${at.toLocaleString("en-US")} tokens`;
  if (stop.kind === "messages")
    return `Stopped at ${at.toLocaleString("en-US")} ${at === 1 ? "message" : "messages"}`;
  if (stop.kind === "cost_usd") return `Stopped at ${fmtCost(at)} for this row`;
  if (stop.kind === "timeout_s") return `Timed out after ${at} s`;
  return null;
}

/** Total tokens of a step (`{input, output, total}` or a bare number). */
export function stepTokenTotal(step) {
  const t = step?.tokens;
  if (t == null) return 0;
  if (typeof t === "number") return t;
  return Number(t.total) || (Number(t.input) || 0) + (Number(t.output) || 0);
}

/** Whether a step is a model or tool turn (a message of the try). */
export const isMessageStep = (s) =>
  s?.type === "llm" || s?.type === "tool_call";

/**
 * Where a try's steps cross the limits: `{index, kind, at, fraction}` of
 * the last step kept, or null when no limit is crossed. Messages keep the
 * first N turns whole (fraction 1); tokens and cost keep the crossing
 * step's share up to the limit (0 < fraction <= 1).
 */
export function stopOf(steps = [], limits = {}) {
  const L = normalizeRowLimits(limits);
  if (L.tokens == null && L.messages == null && L.cost_usd == null) return null;
  let tokens = 0;
  let messages = 0;
  let cost = 0;
  for (let i = 0; i < steps.length; i += 1) {
    const s = steps[i];
    if (s?.type === "end_state") continue;
    if (isMessageStep(s)) {
      if (L.messages != null && messages + 1 > L.messages)
        return { index: i - 1, kind: "messages", at: L.messages, fraction: 1 };
      messages += 1;
    }
    const t = stepTokenTotal(s);
    const c = Number(s?.cost) || 0;
    // A step that would start at the limit exactly never starts.
    if (L.tokens != null && tokens + t > L.tokens)
      return tokens >= L.tokens
        ? { index: i - 1, kind: "tokens", at: L.tokens, fraction: 1 }
        : {
            index: i,
            kind: "tokens",
            at: L.tokens,
            fraction: t > 0 ? (L.tokens - tokens) / t : 1,
          };
    if (L.cost_usd != null && cost + c > L.cost_usd + 1e-12)
      return cost >= L.cost_usd - 1e-12
        ? { index: i - 1, kind: "cost_usd", at: L.cost_usd, fraction: 1 }
        : {
            index: i,
            kind: "cost_usd",
            at: L.cost_usd,
            fraction: c > 0 ? (L.cost_usd - cost) / c : 1,
          };
    tokens += t;
    cost += c;
  }
  return null;
}

/**
 * Whether an unlimited try would stop under the limits (the walk's and
 * Review's check): the same rule the engine applies.
 */
export const wouldStop = (attempt, limits) =>
  stopOf(attempt?.trajectory?.steps ?? attempt?.steps ?? [], limits);

/** One line for Review: `Limits per row · 600 tokens · 12 messages · $0.05 · 120 s`. */
export function rowLimitsLine(limits) {
  const L = normalizeRowLimits(limits);
  const bits = [];
  if (L.tokens != null) bits.push(`${L.tokens.toLocaleString("en-US")} tokens`);
  if (L.messages != null) bits.push(`${L.messages} messages`);
  if (L.cost_usd != null) bits.push(fmtCost(L.cost_usd));
  if (L.timeout_s != null) bits.push(`${L.timeout_s} s`);
  return bits.length ? `Limits per row · ${bits.join(" · ")}` : null;
}

/**
 * The create-body keys of a card (`{row_limits}`; {} when none set): each
 * value as the field shows it (a number when it reads as one), never
 * rounded; an invalid one is refused by the step, the Try and the API.
 */
export function rowLimitsExtras(card) {
  const raw = card?.rowLimits ?? {};
  const out = {};
  ROW_LIMIT_FIELDS.forEach(({ key }) => {
    const v = raw?.[key];
    if (v == null || v === "") return;
    const n = Number(v);
    out[key] = Number.isFinite(n) ? n : v;
  });
  return Object.keys(out).length ? { row_limits: out } : {};
}
