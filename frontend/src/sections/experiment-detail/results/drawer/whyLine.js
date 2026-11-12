// R3 MUST 8 (SVP-4.10): the result drawer's first line.
//   Why it failed: #2B read A-8830 as "8830" (step 1, classify_intent). #2C passed.
//   Why it passed: #2C passed all 3 trials. #2B failed.
//   Mixed: #2E passed 2 of 3 trials; Order found failed. #2C passed.
// Pure: outcomes from the row's results, the step from the R5 cause
// (G0 numbering: `first_divergence.step_n` = the failing lane's own step).
//
// iterate r3 G3 (P1): opened from a regressions list (`regression`), the
// line is about the run that got worse on this row, never `Why it passed`:
//   Regression in #7B: never called cancel_order (expected
//   cancel_order{order_id: A-6118, item: kettle}); replied as if it had.
// The steps are cross-checked first: 0 tool calls where the row expects
// some is the reason, whatever the judge wrote. `No run passed every trial`
// only when some run has more than one trial.
import { causeMarker } from "../../matrix/matrixModel";
import { checksOf, trialOutcome, whyChips } from "../resultsSimple";
import { outputText } from "../outputText";

const lowerFirst = (s) => (s ? s[0].toLowerCase() + s.slice(1) : s);
const upperFirst = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** The row's expected tool calls (`expected_tool_calls`), or []. */
export function expectedCallsOf(expected) {
  const v = expected?.value ?? expected;
  const calls = v?.expected_tool_calls ?? v?.tool_calls;
  return Array.isArray(calls) ? calls.filter((c) => c && c.name) : [];
}

/** `cancel_order{order_id: A-6118, item: kettle}`. */
export function callText(call) {
  const args = Object.entries(call?.args ?? call?.arguments ?? {})
    .map(
      ([k, v]) => `${k}: ${v && typeof v === "object" ? JSON.stringify(v) : v}`,
    )
    .join(", ");
  return `${call?.name ?? "tool"}{${args}}`;
}

/** Tool calls of one trial (trace summary), else of the result; null = unknown. */
export function toolCallsOf(result, trial = 1) {
  const ts = result?.trace_summary;
  if (Array.isArray(ts) && ts.length) {
    const t = ts[Math.max(0, (trial ?? 1) - 1)] ?? ts[0];
    if (Number.isFinite(t?.tool_calls)) return t.tool_calls;
  }
  const n = result?.steps?.tool_calls;
  return Number.isFinite(n) ? n : null;
}

const CLAIMS =
  /\b(I've|I have|taken care|done|processed|cancell?ed|issued|escalated|updated|passed this|refunded|scheduled|booked|changed)\b/i;

/**
 * `never called cancel_order (expected cancel_order{order_id: A-6118, item:
 * kettle}); replied as if it had` when the run made 0 tool calls on a row
 * that expects some, else null.
 */
export function neverCalledText(result, expected, trial = 1, tools = null) {
  const calls = expectedCallsOf(expected);
  if (!calls.length || toolCallsOf(result, trial) !== 0) return null;
  const names = [...new Set(calls.map((c) => c.name))];
  const claimed = CLAIMS.test(outputText(result?.output));
  // IT-R5 G3 (S2): not attached vs attached but not called, when the run's
  // tools are known (`tools` = {names: string[], noun: "prompt"|"run"}).
  if (Array.isArray(tools?.names)) {
    const have = new Set(tools.names);
    const missing = names.filter((n) => !have.has(n));
    const idle = names.filter((n) => have.has(n));
    const bits = [];
    if (missing.length)
      bits.push(
        `${joinLabels(missing)} ${missing.length === 1 ? "is" : "are"} not attached to this ${tools.noun ?? "run"}`,
      );
    if (idle.length)
      bits.push(
        `${joinLabels(idle)} ${idle.length === 1 ? "was" : "were"} attached but not called`,
      );
    return `${bits.join("; ")}${claimed ? "; replied as if it had" : ""}`;
  }
  return `never called ${joinLabels(names)} (expected ${calls.map(callText).join(", ")})${claimed ? "; replied as if it had" : ""}`;
}

/** Expected tools the run does not have attached (for `Attach it`). Pure. */
export function missingToolsOf(result, expected, trial = 1, tools = null) {
  const calls = expectedCallsOf(expected);
  if (!calls.length || !Array.isArray(tools?.names)) return [];
  if (toolCallsOf(result, trial) !== 0) return [];
  const have = new Set(tools.names);
  return [...new Set(calls.map((c) => c.name))].filter((n) => !have.has(n));
}

// Eval metrics only: a latency / cost move is not a regression of the answer.
const movedOn = (row, id, dir, defsByKey = {}) =>
  Object.entries(row?.results?.[id]?.movement ?? {}).some(([k, m]) => {
    if (m !== dir) return false;
    const def = defsByKey[k];
    return def
      ? !def.kind || def.kind === "eval"
      : String(k).startsWith("eval:");
  });

/** `#2A`, `#2A and #2C`, `#2A, #2C and #2D`. */
export function joinLabels(labels) {
  const list = (labels ?? []).filter(Boolean);
  if (list.length <= 1) return list[0] ?? "";
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/** An error value (`"…"`, `{text}`, `{message}`) as text, else null. */
const errorTextOf = (e) => {
  if (!e) return null;
  if (typeof e === "string") return e.trim() || null;
  const t = e.text ?? e.message ?? null;
  return typeof t === "string" && t.trim() ? t.trim() : null;
};

/**
 * options-truth L3 G10 (why-line-record-error): the error the try's record
 * carries (`Network off: api.stripe.com unreachable`, `No recording for
 * lookup_order on this row`, a timeout), for that try when the result has
 * per-try detail, else the result's own error; null when it ran clean.
 */
export function recordErrorOf(result, trial = 1) {
  const detail = Array.isArray(result?.trials_detail)
    ? result.trials_detail.find((t) => t?.trial === trial) ??
      result.trials_detail[Math.max(0, (trial ?? 1) - 1)]
    : null;
  return errorTextOf(detail?.error) ?? errorTextOf(result?.error);
}

/** The first sentence of an eval reason, without its trailing period. */
const firstSentence = (reason) =>
  String(reason ?? "")
    .split(/[.;]\s/)[0]
    .replace(/[.;]\s*$/, "")
    .trim();

/**
 * `{expected: "A-8830", got: "8830"}` when a check expects a string that
 * contains the value the cause's step returned (the step's output), else
 * null.
 */
export function readAsOf(expected, returned) {
  if (!returned || typeof returned !== "string") return null;
  const values = (checksOf(expected) ?? [])
    .map((c) => c?.assert?.value)
    .filter((v) => typeof v === "string");
  const hit = values.find((v) => v !== returned && v.includes(returned));
  return hit ? { expected: hit, got: returned } : null;
}

// L7c merge: the kinds whose tool calls happen on the user's side.
const ENDPOINT_RUN_KINDS = new Set(["http", "a2a", "mcp_tool"]);
const endpointRun = (run) =>
  ENDPOINT_RUN_KINDS.has(
    run?.config?.kind ??
      run?.source?.kind ??
      run?.kind ??
      run?.target_type ??
      run?.targetType,
  );

/**
 * The drawer's why line.
 * IT-R4 G3 (P5): names only the pair in view — the run and the page's
 * baseline (`Why it passed: #7B passed; baseline #1A failed: …`); on the
 * baseline itself, the page's candidate (`candidateId`).
 * @param {{row, runs: {run_id, label}[], focusedRunId, baselineId,
 *   primaryKey, causes?: object[], returned?: string|null,
 *   candidateId?: string|null}} args
 * @returns {{tone: "fail"|"pass"|"flaky"|"none", text: string,
 *   subjectId: string|null, marker: object|null, failing?: boolean}}
 *   `failing`: the subject is the pair's failing run (its trace is read)
 *   even though the line's tone is the passing run's.
 */
export function whyLine({
  row,
  runs = [],
  focusedRunId,
  baselineId,
  primaryKey,
  causes = [],
  returned = null,
  regression = false,
  defsByKey = {},
  trial = 1,
  candidateId = null,
  toolsByRun = {},
}) {
  const outcome = (id) => trialOutcome(row?.results?.[id], primaryKey);
  const label = (id) => runs.find((r) => r.run_id === id)?.label ?? "";
  const inView = (id) => Boolean(id) && runs.some((r) => r.run_id === id);
  const focused = outcome(focusedRunId);
  const base = baselineId ? outcome(baselineId) : null;
  let subject = focusedRunId;
  // A regressions list: the run that got worse here (the focused one when
  // it did, else the first that did).
  const worse = regression
    ? runs
        .map((r) => r.run_id)
        .filter(
          (id) => id !== baselineId && movedOn(row, id, "regressed", defsByKey),
        )
    : [];
  // IT-R4 G3 (P5): the line is about the pair in view — the run and the
  // page's baseline (on the baseline itself: the page's candidate) — never
  // a third run.
  const pairOf = (id) =>
    id !== baselineId && inView(baselineId)
      ? baselineId
      : [focusedRunId, candidateId].find(
          (x) => x && x !== baselineId && inView(x),
        ) ?? null;
  const roleLabel = (id) =>
    id === baselineId && inView(id) && runs.length > 1
      ? `baseline ${label(id)}`
      : label(id);
  const statusOf = (id) => {
    const o = outcome(id);
    if (o.tone === "pass") return "passed";
    if (o.tone === "fail") return "failed";
    if (o.tone === "flaky") return `passed ${o.pass} of ${o.n} trials`;
    return "has no result yet";
  };
  /** `. Baseline #1A failed too` — the other run of the pair, or "". */
  const pairTail = (id) => {
    const other = pairOf(id);
    if (!other) return "";
    const same = outcome(other).tone === outcome(id).tone;
    return `. ${upperFirst(roleLabel(other))} ${statusOf(other)}${same && outcome(id).tone !== "flaky" ? " too" : ""}`;
  };
  // What went wrong on a failing run, and where (step chip).
  const failureOf = (id) => {
    const cause = causes.find(
      (c) =>
        c.run_id === id &&
        (c.rows ?? []).some((r) => r.row_id === row?.example_id),
    );
    const marker = causeMarker(cause, label(id));
    const result = row?.results?.[id];
    // The steps first: no tool call where the row expects one IS the reason.
    // L7c merge (result:endpoint-runs, g2 owed): an endpoint, A2A agent or
    // MCP tool run calls its tools on its own side, where Future AGI cannot
    // see them, so `never called …` would be a false reason there.
    const never = endpointRun(runs.find((r) => r.run_id === id))
      ? null
      : neverCalledText(
          result,
          row?.expected,
          id === focusedRunId ? trial : 1,
          toolsByRun?.[id] ?? null,
        );
    const readAs = !never && marker ? readAsOf(row?.expected, returned) : null;
    const reason = firstSentence(result?.scores?.[primaryKey]?.reason);
    // The record's own error beats the judge's words about it.
    const recordError = never
      ? null
      : recordErrorOf(result, id === focusedRunId ? trial : 1);
    let what = "failed its checks";
    if (never) what = never;
    else if (readAs) what = `read ${readAs.expected} as "${readAs.got}"`;
    else if (recordError) what = recordError.replace(/[.;]\s*$/, "");
    else if (cause?.title) what = lowerFirst(cause.title);
    else if (reason) what = lowerFirst(reason);
    const step =
      marker && !never ? ` (step ${marker.stepN}, ${marker.stepName})` : "";
    return { what, step, marker: never ? null : marker };
  };

  if (worse.length)
    subject = worse.includes(focusedRunId) ? focusedRunId : worse[0];
  else if (
    focusedRunId !== baselineId &&
    focused.tone === "pass" &&
    base &&
    ["fail", "flaky"].includes(base.tone)
  ) {
    // `Why it passed: #7B passed; baseline #1A failed: <why>.` — the
    // baseline's failure is the reason (its step chip, its trace).
    const f = failureOf(baselineId);
    const trials = focused.n > 1 ? ` all ${focused.n} trials` : "";
    return {
      tone: "pass",
      text: `Why it passed: ${label(focusedRunId)} passed${trials}; ${roleLabel(baselineId)} ${statusOf(baselineId)}: ${f.what}${f.step}.`,
      subjectId: baselineId,
      failing: true,
      marker: f.marker,
    };
  }
  const sub = outcome(subject);
  if (sub.tone === "none")
    return { tone: "none", text: "", subjectId: subject, marker: null };
  const isRegression = worse.includes(subject);
  const who = label(subject);
  const tail = pairTail(subject);
  if (sub.tone === "pass" && isRegression) {
    // Passed the primary check; another eval got worse vs the baseline.
    const chip = whyChips(row, {
      runIds: [baselineId, subject],
      baselineId,
      defsByKey,
      show: "regressed",
      max: 1,
    })[0];
    const trials = sub.n > 1 ? ` all ${sub.n} trials` : "";
    return {
      tone: "flaky",
      text: `Regression in ${who}: ${chip ? chip.text : "an eval scored lower"} vs ${label(baselineId) || "the baseline"}; ${defsByKey[primaryKey]?.name ?? "it"} still passed${trials}.`,
      subjectId: subject,
      marker: null,
    };
  }
  if (sub.tone === "pass") {
    const trials = sub.n > 1 ? ` all ${sub.n} trials` : "";
    return {
      tone: "pass",
      text: `Why it passed: ${who} passed${trials}${tail}.`,
      subjectId: subject,
      marker: null,
    };
  }
  const f = failureOf(subject);
  if (isRegression)
    return {
      tone: sub.tone === "flaky" ? "flaky" : "fail",
      text:
        sub.tone === "flaky"
          ? `Regression in ${who}: passed ${sub.pass} of ${sub.n} trials; ${f.what}${f.step}${tail}.`
          : `Regression in ${who}: ${upperFirst(f.what)}${f.step}${tail}.`,
      subjectId: subject,
      marker: f.marker,
    };
  if (sub.tone === "flaky")
    return {
      tone: "flaky",
      text: `Mixed: ${who} passed ${sub.pass} of ${sub.n} trials; ${f.what}${f.step}${tail}.`,
      subjectId: subject,
      marker: f.marker,
    };
  return {
    tone: "fail",
    text: `Why it failed: ${who} ${f.what}${f.step}${tail}.`,
    subjectId: subject,
    marker: f.marker,
  };
}
