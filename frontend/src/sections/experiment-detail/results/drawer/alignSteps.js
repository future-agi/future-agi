// Trajectory steps and their aligned diff (PRODUCT-SPEC §3.11 Trajectory
// tab). Pure: no React, no I/O.
//
// Steps = the span tree of `GET /tracer/trace/{id}/` flattened by start
// time, root excluded. Two trajectories align with an LCS on `type:name`;
// matched steps compare input and output, unmatched steps are "only in" one
// run. The trajectory *diverges* at the first step whose action differs:
// a step only one run took, a changed input, or an error on one side. A
// changed output alone is not a divergence (its effect shows up as the next
// step's input).

import {
  flattenTrace,
  normalizeSteps,
  sameValue,
  stableJson,
  stepFromSpan,
} from "src/sections/common/Align/normalize";
import { fmtMs } from "src/sections/common/format/fmt";
import {
  alignLanes,
  markers as laneMarkers,
  toLaneSteps,
} from "src/sections/common/Align/milestones";

// R3 (G0 B9): one copy of the step normalisers (common/Align/normalize.js);
// cross-kind alignment delegates to G0's milestone alignment.
export { flattenTrace, normalizeSteps, sameValue, stableJson, stepFromSpan };

const MAX_PREVIEW = 42;

const spanOf = (entry) => entry?.observation_span ?? entry ?? null;

/** The root span of a trace (for totals), or null. */
export const traceRoot = (trace) => spanOf(trace?.observation_spans?.[0]);

/** `{steps, llmCalls, toolCalls, toolErrors, retrievals, ms, tokens, cost}`. */
export function stepTotals(steps, root = null) {
  const list = steps ?? [];
  const tools = list.filter((s) => s.type === "tool");
  const sum = (key) =>
    list.reduce((acc, s) => acc + (Number.isFinite(s[key]) ? s[key] : 0), 0);
  return {
    steps: list.length,
    llmCalls: list.filter((s) => s.type === "llm").length,
    toolCalls: tools.length,
    toolErrors: tools.filter((s) => s.error).length,
    errors: list.filter((s) => s.error).length,
    retrievals: list.filter((s) => s.type === "retriever").length,
    ms: Number.isFinite(root?.latency_ms) ? root.latency_ms : sum("ms"),
    tokens: sum("tokens"),
    cost: sum("cost"),
  };
}

/** LCS alignment of two key lists; matches the earliest pairs first. */
export function lcsPairs(aKeys, bKeys) {
  const n = aKeys.length;
  const m = bKeys.length;
  // suffix table: L[i][j] = LCS of a[i:], b[j:]
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1)
    for (let j = m - 1; j >= 0; j -= 1)
      L[i][j] =
        aKeys[i] === bKeys[j]
          ? L[i + 1][j + 1] + 1
          : Math.max(L[i + 1][j], L[i][j + 1]);
  const pairs = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (aKeys[i] === bKeys[j]) {
      pairs.push([i, j]);
      i += 1;
      j += 1;
    } else if (L[i + 1][j] >= L[i][j + 1]) i += 1;
    else j += 1;
  }
  return pairs;
}

/**
 * Aligned rows. Each row: `{index (1-based), a, b, kind, inputChanged,
 * outputChanged, error}` where kind is `same | changed | only_a | only_b`.
 * Unmatched steps between two matches list run A's first, then run B's.
 */
export function alignSteps(aSteps, bSteps) {
  const a = aSteps ?? [];
  const b = bSteps ?? [];
  const pairs = lcsPairs(
    a.map((s) => s.key),
    b.map((s) => s.key),
  );
  const rows = [];
  let i = 0;
  let j = 0;
  const flush = (untilA, untilB) => {
    for (; i < untilA; i += 1)
      rows.push({
        a: a[i],
        b: null,
        kind: "only_a",
        error: Boolean(a[i].error),
      });
    for (; j < untilB; j += 1)
      rows.push({
        a: null,
        b: b[j],
        kind: "only_b",
        error: Boolean(b[j].error),
      });
  };
  pairs.forEach(([pi, pj]) => {
    flush(pi, pj);
    const sa = a[pi];
    const sb = b[pj];
    const inputChanged = !sameValue(sa.input, sb.input);
    const errorChanged = Boolean(sa.error) !== Boolean(sb.error);
    // An error on one side is its own badge, not an "output changed".
    const outputChanged =
      !errorChanged &&
      (!sameValue(sa.output, sb.output) || sa.error !== sb.error);
    rows.push({
      a: sa,
      b: sb,
      kind: inputChanged || outputChanged || errorChanged ? "changed" : "same",
      inputChanged,
      outputChanged,
      error: Boolean(sa.error || sb.error),
    });
    i = pi + 1;
    j = pj + 1;
  });
  flush(a.length, b.length);
  return rows.map((row, k) => ({
    inputChanged: false,
    outputChanged: false,
    ...row,
    index: k + 1,
  }));
}

/** Badges of a row, in display order. */
export function rowBadges(row, { labelA, labelB }) {
  if (row.kind === "only_a") {
    const out = [{ kind: "only", text: `Only in ${labelA}` }];
    if (row.error) out.push({ kind: "error", text: "Error" });
    return out;
  }
  if (row.kind === "only_b") {
    const out = [{ kind: "only", text: `Only in ${labelB}` }];
    if (row.error) out.push({ kind: "error", text: "Error" });
    return out;
  }
  const out = [];
  if (row.inputChanged) out.push({ kind: "input", text: "Input changed" });
  if (row.outputChanged) out.push({ kind: "output", text: "Output changed" });
  if (row.error) out.push({ kind: "error", text: "Error" });
  if (!out.length) out.push({ kind: "same", text: "Same" });
  return out;
}

// A simulated customer turn or the End state on one side only is context,
// not an action the agent took differently (R2 harness pairs).
const isContext = (step) => step?.nkind === "customer" || step?.nkind === "end";

/** True for rows where the action differs (see the header comment). */
export const isDivergent = (row) =>
  !row.sub &&
  ((row.kind === "only_a" && !isContext(row.a)) ||
    (row.kind === "only_b" && !isContext(row.b)) ||
    row.inputChanged ||
    (Boolean(row.a?.error) !== Boolean(row.b?.error) &&
      !isContext(row.a) &&
      !isContext(row.b)));

const clip = (text) => {
  const s = String(text);
  return s.length > MAX_PREVIEW ? `${s.slice(0, MAX_PREVIEW - 1)}…` : s;
};
const show = (v) =>
  v === undefined
    ? "—"
    : typeof v === "string"
      ? `"${clip(v)}"`
      : clip(JSON.stringify(v));

/**
 * The first differing field of two inputs: `order_id "A-7781" → "7781"`,
 * `order_id → order_number` (renamed key), or the two values.
 */
export function inputChangeText(before, after) {
  const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
  if (isObj(before) && isObj(after)) {
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
    const changed = keys.filter((k) => !sameValue(before[k], after[k]));
    const removed = changed.filter((k) => !(k in after));
    const added = changed.filter((k) => !(k in before));
    if (removed.length === 1 && added.length === 1 && changed.length === 2)
      return `${removed[0]} ${show(before[removed[0]])} → ${added[0]} ${show(after[added[0]])}`;
    const k =
      changed.find((key) => key in before && key in after) ?? changed[0];
    if (k == null) return "";
    return `${k} ${show(before[k])} → ${show(after[k])}`;
  }
  return `${show(before)} → ${show(after)}`;
}

/**
 * Where two trajectories diverge: `{row, index, text}` or null when every
 * aligned step takes the same action. `text` reads
 * `lookup_order input changed (order_id "A-7781" → "7781")`.
 */
export function divergence(rows, { labelA, labelB }) {
  const row = (rows ?? []).find(isDivergent);
  if (!row) return null;
  let text;
  if (row.kind === "only_a") text = `${row.a.name} only in ${labelA}`;
  else if (row.kind === "only_b") text = `${row.b.name} only in ${labelB}`;
  else if (row.inputChanged)
    text = `${row.a.name} input changed (${inputChangeText(row.a.input, row.b.input)})`;
  else {
    const failed = row.b?.error ? row.b : row.a;
    const who = row.b?.error ? labelB : labelA;
    text = `${failed.name} failed in ${who} (${failed.error})`;
  }
  return { row, index: row.index, text };
}

/** `6 steps · 2 tool calls · 2 tool errors · 2.9 s` for a totals object. */
export function totalsText(t) {
  if (!t) return "";
  // A prompt run: one (or a few) LLM calls and nothing else.
  const llmOnly = t.steps > 0 && t.llmCalls === t.steps;
  const parts = llmOnly
    ? [`${t.llmCalls} LLM ${t.llmCalls === 1 ? "call" : "calls"}`]
    : [
        `${t.steps} ${t.steps === 1 ? "step" : "steps"}`,
        `${t.toolCalls} tool ${t.toolCalls === 1 ? "call" : "calls"}`,
      ];
  if (t.toolErrors)
    parts.push(
      `${t.toolErrors} tool ${t.toolErrors === 1 ? "error" : "errors"}`,
    );
  if (Number.isFinite(t.ms)) parts.push(formatMs(t.ms));
  return parts.join(" · ");
}

/** `412 ms` · `2.9 s` · `74 s` (from 30 s) · `3 min` (from 2 min). */
export function formatMs(ms) {
  // R3 F1 / RC#37: one formatter (G0 fmt.js): `842 ms` `9.8 s` `74 s` `12 m 05 s`.
  return fmtMs(ms);
}

/** The trace id of a run's result for a 1-based trial (falls back to trial 1). */
export function traceIdFor(result, trial = 1) {
  if (!result) return null;
  const detail = result.trials_detail ?? [];
  const byTrial = detail.find((t) => Number(t.trial) === Number(trial));
  if (byTrial?.trace_id) return byTrial.trace_id;
  const ids = result.trace_ids ?? [];
  return ids[Number(trial) - 1] ?? ids[0] ?? detail[0]?.trace_id ?? null;
}

/** Trials a result has (`trials`, `trials_detail`, `trace_ids`). */
export const trialCount = (result) =>
  Math.max(
    1,
    Number(result?.trials) || 0,
    result?.trials_detail?.length ?? 0,
    result?.trace_ids?.length ?? 0,
  );

// ---------------------------------------------------------------------------
// R2 (L1): alignment across run kinds and the second banner line.
//
// Prompt runs, agent graphs, your code, harnesses and production traces name
// their steps differently. `normalizeSteps` maps each step to a normalised
// kind (`llm`, `tool`, `customer`, `end`) and, for tools, the bare tool name
// (`mcp__acme-support-sandbox__lookup_order` → `lookup_order`).
// `alignStepsAcross` aligns on (kind, tool name) and prefers pairs whose
// arguments match (weighted LCS); only tool arguments count as an input
// change, because two kinds of run phrase their model calls differently.

const nOf = (s, i) =>
  Number.isFinite(s?.n) ? s.n : Number.isFinite(s?.order) ? s.order + 1 : i + 1;

/**
 * Align two step lists on G0 milestones (R3 MUST 5): tool calls, replies,
 * file edits, commands and the end state align by (kind, tool, arguments)
 * against `a` (the baseline lane); internal model steps (`classify_intent`,
 * `reformulate_query`) ride along before their milestone as `sub` rows and
 * never pair with another run's reply; unmatched steps stay as hatched
 * "only in" rows. Same row shape as alignSteps(); also returns G0's
 * alignment and markers (`firstDifference` numbered by `a`'s own steps).
 * @returns {{rows: object[], alignment: object, markers: object}}
 */
export function alignPairMilestones(
  aSteps,
  bSteps,
  { labelA = "A", labelB = "B", kindA, kindB } = {},
) {
  const a = aSteps ?? [];
  const b = bSteps ?? [];
  const byA = new Map(a.map((s, i) => [nOf(s, i), s]));
  const byB = new Map(b.map((s, i) => [nOf(s, i), s]));
  const laneA = {
    id: "a",
    label: labelA,
    steps: toLaneSteps(a, { runKind: kindA }),
  };
  const laneB = {
    id: "b",
    label: labelB,
    steps: toLaneSteps(b, { runKind: kindB }),
  };
  const alignment = alignLanes(laneA, [laneB], { mode: "milestone" });
  const m = laneMarkers({ rows: alignment.rows, lanes: [laneA, laneB] });
  const rows = [];
  const only = (side, st, extra) => {
    const orig = (side === "a" ? byA : byB).get(st.n);
    if (!orig) return;
    rows.push(
      side === "a"
        ? {
            a: orig,
            b: null,
            kind: "only_a",
            error: Boolean(orig.error),
            ...extra,
          }
        : {
            a: null,
            b: orig,
            kind: "only_b",
            error: Boolean(orig.error),
            ...extra,
          },
    );
  };
  alignment.rows.forEach((row) => {
    const [ca, cb] = row.cells;
    if (row.kind === "gap") {
      if (ca?.type === "gap")
        ca.steps.forEach((st) =>
          only("a", st, { gap: row.id, sub: st.kind === "think" }),
        );
      if (cb?.type === "gap")
        cb.steps.forEach((st) =>
          only("b", st, { gap: row.id, sub: st.kind === "think" }),
        );
      return;
    }
    (ca?.subSteps ?? []).forEach((st) =>
      only("a", st, { sub: true, mid: row.id }),
    );
    (cb?.subSteps ?? []).forEach((st) =>
      only("b", st, { sub: true, mid: row.id }),
    );
    const sa = ca?.step ? byA.get(ca.step.n) : null;
    const sb = cb?.step ? byB.get(cb.step.n) : null;
    if (sa && sb) {
      const inputChanged = Boolean(cb.inputChanged);
      const errorChanged = Boolean(cb.errorChanged);
      const outputChanged = !errorChanged && Boolean(cb.outputChanged);
      rows.push({
        a: sa,
        b: sb,
        kind:
          inputChanged || outputChanged || errorChanged ? "changed" : "same",
        inputChanged,
        outputChanged,
        error: Boolean(sa.error || sb.error),
        mid: row.id,
      });
    } else if (sa) only("a", ca.step, { mid: row.id });
    else if (sb) only("b", cb.step, { mid: row.id });
  });
  return {
    rows: rows.map((row, k) => ({
      inputChanged: false,
      outputChanged: false,
      ...row,
      index: k + 1,
    })),
    alignment,
    markers: m,
  };
}

/**
 * Align two normalised step lists across run kinds (delegates to G0
 * milestone alignment; see alignPairMilestones). Same row shape as
 * alignSteps().
 */
export function alignStepsAcross(aSteps, bSteps, opts) {
  return alignPairMilestones(aSteps, bSteps, opts).rows;
}

/**
 * The display row of G0's first difference (for line 2 and the highlight),
 * from alignPairMilestones() rows, or null.
 */
export function rowOfFirstDifference(rows, firstDifference) {
  if (!firstDifference || !rows?.length) return null;
  const id = firstDifference.rowId;
  return (
    rows.find((r) => !r.sub && r.mid === id && r.kind !== "same") ??
    rows.find((r) => !r.sub && r.gap === id) ??
    rows.find((r) => r.mid === id) ??
    null
  );
}

/** Gutter glyph of an aligned row: `=` same · `~` changed · `+` only right · `−` only left. */
export const gutterGlyph = (row) =>
  row.kind === "same"
    ? "="
    : row.kind === "changed"
      ? "~"
      : row.kind === "only_b"
        ? "+"
        : "−";

const quoted = (v) => (typeof v === "string" ? `"${v}"` : JSON.stringify(v));

/** HTTP-like status of a tool call: the error code, else 200. */
function callStatus(step) {
  if (!step) return null;
  if (step.error) {
    const m = /^(\d{3})\b/.exec(String(step.error));
    return m ? Number(m[1]) : "an error";
  }
  return 200;
}

/** The first argument that differs: `{key, a, b}` or null. */
export function changedArg(before, after) {
  const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
  if (!isObj(before) || !isObj(after)) return null;
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  const k = keys.find((key) => !sameValue(before[key], after[key]));
  return k == null ? null : { key: k, a: before[k], b: after[k] };
}

/**
 * The second banner line: what happened at the divergence and why.
 * `#2B got 404 for "8830"; #2A got 200 for "A-8830". The input came from
 * step 1: classify_intent returned "8830" (prompt: "…").` For two
 * production traces (no runs) the second sentence says which agent version
 * ran: `Both traces ran Support Agent v3.`
 * @param {object} div  divergence() result
 * @param {{labelA, labelB, stepsA, promptOf?: (node) => string|null,
 *   agentA?: string|null, agentB?: string|null}} ctx
 * @returns {{text: string|null, modeNote: string|null}}
 */
export function explainDivergence(div, ctx = {}) {
  const row = div?.row;
  if (!row || !row.a || !row.b) return { text: null, modeNote: null };
  const { labelA, labelB } = ctx;
  const parts = [];
  const arg = row.inputChanged ? changedArg(row.a.input, row.b.input) : null;
  if (arg && row.a.nkind !== "llm" && row.a.type !== "llm") {
    parts.push(
      `${labelA} got ${callStatus(row.a)} for ${quoted(arg.a)}; ${labelB} got ${callStatus(row.b)} for ${quoted(arg.b)}.`,
    );
    if (ctx.agentA !== undefined || ctx.agentB !== undefined) {
      if (ctx.agentA && ctx.agentA === ctx.agentB)
        parts.push(`Both traces ran ${ctx.agentA}.`);
      else if (ctx.agentA || ctx.agentB)
        parts.push(
          `${labelA} ran ${ctx.agentA ?? "an unknown agent"}; ${labelB} ran ${ctx.agentB ?? "an unknown agent"}.`,
        );
    } else {
      const needle = typeof arg.a === "string" ? arg.a : null;
      const before = (ctx.stepsA ?? []).filter(
        (s) => s !== row.a && s.order < row.a.order,
      );
      const source = needle
        ? [...before]
            .reverse()
            .find((s) => stableJson(s.output).includes(JSON.stringify(needle)))
        : null;
      if (source) {
        const prompt = ctx.promptOf?.(source.name) ?? null;
        parts.push(
          `The input came from step ${source.order + 1}: ${source.name} returned ${quoted(needle)}${prompt ? ` (prompt: "${prompt}")` : ""}.`,
        );
      }
    }
  } else if (row.a.error || row.b.error) {
    const failed = row.b.error ? row.b : row.a;
    parts.push(
      `${row.b.error ? labelB : labelA}: ${failed.name} returned ${failed.error}.`,
    );
  }
  let modeNote = null;
  const tool = row.a.toolName ?? row.a.name;
  if (row.b.mode === "mock" && row.a.mode !== "mock")
    modeNote = `${tool} is mocked in ${labelB}, so this difference is the agent's, not the tool's.`;
  else if (row.a.mode === "mock" && row.b.mode !== "mock")
    modeNote = `${tool} is mocked in ${labelA}, so this difference is the agent's, not the tool's.`;
  return { text: parts.length ? parts.join(" ") : null, modeNote };
}

/** `Support Agent v3` from a trace root's attributes, or null. */
export function traceAgentOf(trace) {
  const attrs = traceRoot(trace)?.span_attributes ?? {};
  const graph = attrs["agent.graph"];
  const version = attrs["agent.version"];
  if (!graph && !version) return null;
  return [graph, version].filter(Boolean).join(" ");
}
