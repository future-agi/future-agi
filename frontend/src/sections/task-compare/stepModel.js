// Side by side · one view (ONE-VIEW-SPEC §7.2): a step is ONE plain line
// that opens in place to its exact call. This merges the old Story text
// (storyModel: the hand-authored beats of T26 / T1 / T20, else templates)
// with the old Steps data (name, arguments, result, ms, tokens, cost) per
// alignment row and lane. No React; tested in __tests__/oneView.test.js.
import { isChatter, milestoneBeat, thinkBeat } from "./storyModel";
import { splitBeat } from "./storyGrid";
import { gapId, lineId } from "./oneViewParams";

const parse = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};
const short = (v, max) => {
  const s = String(v ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};
const literal = (v) =>
  typeof v === "string" ? `"${short(v, 48)}"` : short(JSON.stringify(v), 48);

/** `{order_id: "8830"}` (every key, compact) · `3 messages` · text. */
export function callArgs(input) {
  const v = parse(input);
  if (v == null || v === "") return "";
  if (Array.isArray(v))
    return `${v.length} ${v.length === 1 ? "message" : "messages"}`;
  if (typeof v === "object") {
    const parts = Object.entries(v)
      .filter(([, x]) => x != null && x !== "")
      .map(([k, x]) => `${k}: ${literal(x)}`);
    return `{${parts.join(", ")}}`;
  }
  return literal(v);
}

/** One-line result: the error, `{id: "A-8830", status: "delivered"}`, `3 items`, text. */
export function callResult(step) {
  if (step?.error) return short(step.error, 120);
  const v = parse(step?.output);
  if (v == null || v === "") return "";
  if (Array.isArray(v))
    return `${v.length} ${v.length === 1 ? "item" : "items"}`;
  if (typeof v === "object") return callArgs(v);
  return `"${short(v, 120)}"`;
}

/** `lookup_order {order_id: "8830"} → 404 Order 8830 not found`. */
export function exactCall(step) {
  if (!step) return "";
  const args = step.kind === "customer" ? "" : callArgs(step.input);
  const out = callResult(step);
  return `${step.name}${args ? ` ${args}` : ""}${out ? ` → ${out}` : ""}`;
}

/**
 * Plain-language text for every step of a lane, by step number: the
 * hand-authored story beat when the lane has one, else the templates
 * (story-worthy model steps, actions, replies; interim chatter has none).
 * @param {{steps: object[], story?: {text, step_n, tone}[]}} lane
 * @returns {Map<number, {text: string, tone: string}>}
 */
export function laneBeats(lane) {
  const out = new Map();
  if (lane?.story?.length) {
    lane.story.forEach((b) =>
      out.set(b.step_n, { text: b.text, tone: b.tone ?? "neutral" }),
    );
    return out;
  }
  const steps = lane?.steps ?? [];
  const lastReplyN = [...steps].reverse().find((s) => s.kind === "reply")?.n;
  let seenCustomer = false;
  steps.forEach((s) => {
    let text = null;
    if (s.kind === "think") text = thinkBeat(s);
    else if (!isChatter(s, s.n === lastReplyN)) {
      text = milestoneBeat(s, { firstCustomer: !seenCustomer });
      if (s.kind === "customer") seenCustomer = true;
    }
    if (text) out.set(s.n, { text, tone: s.error ? "bad" : "neutral" });
  });
  return out;
}

/** The text a step reads as when no beat covers it. */
function fallbackText(step) {
  if (step.kind === "think") {
    const out = callResult(step);
    return `${step.name}${out ? ` — ${out}` : ""}`;
  }
  return milestoneBeat(step) ?? step.name ?? "Step";
}

/** Steps of one alignment cell, in step order (riding model calls first). */
export function cellSteps(cell) {
  if (!cell) return [];
  if (cell.type === "gap" || cell.type === "list") return cell.steps ?? [];
  return [...(cell.subSteps ?? []), cell.step].filter(Boolean);
}

/**
 * The lines of one lane's cell in one alignment row.
 * Level `milestones` (default) shows actions, replies and the model steps
 * that have a plain-language beat; `calls` / `all` show every model call.
 * The first wrong step and a step that failed a check always show.
 * @param {{cell, beats: Map, level?: string, firstWrongN?: number|null,
 *   failedOf?: (n) => string[]}} args
 * @returns {{step, n, text, action, result, tone, status, firstWrong,
 *   failed: string[], milestone: boolean}[]}
 */
export function cellLines({
  cell,
  beats,
  level = "milestones",
  firstWrongN = null,
  failedOf = () => [],
}) {
  const steps = cellSteps(cell);
  const milestoneN = cell?.type === "step" ? cell.step?.n : null;
  return steps
    .map((step) => {
      const beat = beats?.get(step.n) ?? null;
      const failed = failedOf(step.n) ?? [];
      const firstWrong = firstWrongN != null && step.n === firstWrongN;
      const isCall = step.kind === "think";
      const shown =
        level !== "milestones" ||
        !isCall ||
        Boolean(beat) ||
        firstWrong ||
        failed.length > 0;
      if (!shown) return null;
      const text = beat?.text ?? fallbackText(step);
      const { action, result } = splitBeat(text);
      const bad = Boolean(step.error) || failed.length > 0;
      return {
        step,
        n: step.n,
        text,
        action,
        result,
        tone: bad ? "bad" : beat?.tone ?? "neutral",
        status: bad ? "fail" : beat?.tone === "bad" ? "warn" : "ok",
        firstWrong,
        failed,
        milestone: step.n === milestoneN || !isCall,
      };
    })
    .filter(Boolean);
}

/** `Only in the coding harness Code · 2 steps (1 reply, 1 customer turn)`. */
export function gapLabel(cell, laneName) {
  if (!cell) return "";
  const n = cell.count ?? cell.steps?.length ?? 0;
  return `Only in ${laneName} · ${n} ${n === 1 ? "step" : "steps"}${cell.summary ? ` (${cell.summary})` : ""}`;
}

/**
 * The alignment row (and lane index) that holds step `n` of lane `laneIdx`.
 * @returns {object|null} the row
 */
export function rowOfStep(rows, laneIdx, n) {
  return (
    (rows ?? []).find((r) =>
      cellSteps(r.cells?.[laneIdx]).some((s) => s.n === n),
    ) ?? null
  );
}

/**
 * Every rendered line of an alignment, per row and lane, plus the outline
 * ids Expand all / Collapse all act on.
 * @param {{rows: object[], lanes: {id, steps, story?, checks?}[], level?:
 *   string, firstWrong?: {laneId, stepN}|null}} args  lanes base first
 * @returns {{rows: {row, cells: {lines: object[]}[]}[], ids: string[]}}
 */
export function stepRows({
  rows = [],
  lanes = [],
  level = "milestones",
  firstWrong = null,
} = {}) {
  const beats = lanes.map((l) => laneBeats(l));
  const failedOf = lanes.map((l) => {
    const by = new Map();
    (l.checks ?? [])
      .filter((c) => c.passed === false && Number.isFinite(c.cause_step_n))
      .forEach((c) =>
        by.set(c.cause_step_n, [...(by.get(c.cause_step_n) ?? []), c.key]),
      );
    return (n) => by.get(n) ?? [];
  });
  const lastStepN = lanes.map((l) =>
    (l.steps ?? []).reduce((m, x) => (x?.n > m ? x.n : m), -Infinity),
  );
  const ids = [];
  const out = rows.map((row) => ({
    row,
    cells: lanes.map((lane, l) => {
      const cell = row.cells?.[l] ?? null;
      const lines = cellLines({
        cell,
        beats: beats[l],
        level,
        firstWrongN: firstWrong?.laneId === lane.id ? firstWrong.stepN : null,
        failedOf: failedOf[l],
      });
      if (cell?.type === "gap") ids.push(gapId(row.id, lane.id));
      lines.forEach((ln) => ids.push(lineId(row.id, lane.id, ln.n)));
      // IT-R5 G4 P4: a failed or errored try never ends on a green tick:
      // its last step carries the run's final status.
      const lastN = lastStepN[l];
      const ended = ["fail", "error", "infra"].includes(lane.outcome);
      const marked = ended
        ? lines.map((ln) =>
            ln.n === lastN && ln.status === "ok"
              ? {
                  ...ln,
                  status: "fail",
                  final: lane.outcome === "fail" ? "Failed" : "Errored",
                }
              : ln,
          )
        : lines;
      return { cell, lines: marked };
    }),
  }));
  return { rows: out, ids };
}
