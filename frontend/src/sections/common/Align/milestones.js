// Milestone alignment and divergence markers (R3 G0 §B9; side by side §5.4).
// Every divergence sentence on every screen comes from here; screens never
// compute step numbers themselves.
//
// 1. toLaneSteps(steps, {runKind}) — any trajectory (TS world steps, DS
//    trace steps, already-normalised steps) → lane steps
//    `{n, kind, name, mappedFrom?, isMilestone, input, output, error, ms,
//    tokens, cost}`. `n` = the lane's OWN 1-based step number as its step
//    list shows it (root span excluded). Kinds: customer · reply · think ·
//    tool · retrieval · file_edit · command · test · subagent · end.
//    Milestones = every kind but `think`.
// 2. alignLanes(base, others, {mode}) — milestone mode aligns ONLY
//    milestones, keyed (kind class, tool name, normalised-args hash), by a
//    weighted LCS against the BASELINE lane (other lanes never align to each
//    other). `think` steps ride along as `subSteps` of the next milestone of
//    their lane; unmatched steps fold into gap cells with a count and a
//    summary (`9 file reads, 3 edits, 2 test runs`), never forced into a pair.
// 3. markers({rows, lanes, checks}) — `firstWrongStep` (check-based) and
//    `firstDifference` (diff-based, numbered by the baseline lane's own n),
//    plus the frozen copy builders below.
//
// Imports ONLY ./normalize (never results/drawer/alignSteps.js).
import {
  flattenTrace,
  normalizeSteps,
  sameValue,
  stableJson,
} from "./normalize";

export const LANE_KINDS = Object.freeze([
  "customer",
  "reply",
  "think",
  "tool",
  "retrieval",
  "file_edit",
  "command",
  "test",
  "subagent",
  "end",
]);
/** Kinds whose difference is an action the agent took differently. */
export const ACTION_KINDS = Object.freeze([
  "tool",
  "retrieval",
  "file_edit",
  "command",
  "test",
  "subagent",
]);
export const ALIGN_MODES = Object.freeze(["milestone", "index", "none"]);

export const HOW_ALIGNED_HELP =
  "Steps are matched on tool name and arguments; internal model steps ride along with the next action. Unmatched steps are folded, never forced into a pair.";

const MCP_RE = /^mcp__[^_]+(?:_[^_]+)*?__(.+)$/;
const REPLY_NAMES = new Set([
  "respond",
  "reply",
  "final_answer",
  "final",
  "answer",
  "assistant",
]);
const EDIT_TOOLS = new Set([
  "edit",
  "write",
  "multiedit",
  "apply_patch",
  "str_replace",
  "str_replace_editor",
  "create_file",
  "write_file",
  "notebookedit",
]);
const READ_TOOLS = new Set(["read", "glob", "grep", "ls", "read_file", "view"]);
const COMMAND_TOOLS = new Set([
  "bash",
  "shell",
  "exec",
  "exec_command",
  "run_command",
  "terminal",
  "local_shell",
]);
const SUBAGENT_TOOLS = new Set(["task", "agent", "subagent", "dispatch_agent"]);
const TEST_RE =
  /\b(pytest|jest|vitest|npm (run )?test|yarn test|go test|cargo test|mvn test|tox|unittest)\b/i;
const RETRIEVAL_RE = /(search|retriev|vector|knowledge|_kb\b|^kb_)/i;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const countWords = (v) => {
  const text = typeof v === "string" ? v : v == null ? "" : JSON.stringify(v);
  return text.trim() ? text.trim().split(/\s+/).length : 0;
};

function errorOf(step) {
  if (step.error) {
    if (typeof step.error === "string") return step.error;
    const e = step.error;
    return (
      `${e.code != null ? `${e.code} ` : ""}${e.message ?? ""}`.trim() ||
      "Error"
    );
  }
  if (String(step.status ?? "").toLowerCase() === "error") {
    const e = step.output?.error;
    if (e)
      return `${e.code != null ? `${e.code} ` : ""}${e.message ?? ""}`.trim();
    if (step.type === "end_state") return null; // a failed check, not an error
    return step.status_message ?? "Error";
  }
  return null;
}

const tokensOf = (step) =>
  isNum(step.tokens)
    ? step.tokens
    : isNum(step.tokens?.total)
      ? step.tokens.total
      : null;

/** Classify a tool name (bare, MCP prefix removed) into a lane kind. */
function toolKind(name, input, declared) {
  const low = String(name ?? "").toLowerCase();
  if (declared === "retriever" || declared === "retrieval") return "retrieval";
  if (EDIT_TOOLS.has(low)) return "file_edit";
  if (SUBAGENT_TOOLS.has(low)) return "subagent";
  if (COMMAND_TOOLS.has(low)) {
    const cmd =
      typeof input === "string" ? input : input?.command ?? input?.cmd ?? "";
    return TEST_RE.test(String(Array.isArray(cmd) ? cmd.join(" ") : cmd))
      ? "test"
      : "command";
  }
  if (RETRIEVAL_RE.test(low)) return "retrieval";
  return "tool";
}

const isTsStep = (s) =>
  s &&
  ["llm", "tool_call", "node", "customer", "end_state"].includes(s.type) &&
  (isNum(s.n) ||
    s.type === "tool_call" ||
    s.type === "node" ||
    s.type === "end_state");

function laneStep(step, base) {
  return {
    n: base.n,
    kind: base.kind,
    name: base.name,
    ...(base.mappedFrom ? { mappedFrom: base.mappedFrom } : {}),
    isMilestone: base.kind !== "think",
    input: step.input ?? null,
    output: base.output !== undefined ? base.output : step.output ?? null,
    error: errorOf(step),
    ms: isNum(step.ms) ? step.ms : null,
    tokens: tokensOf(step),
    cost: isNum(step.cost) ? step.cost : null,
    ...(step.mode ? { mode: step.mode } : {}),
    ...(base.toolName ? { toolName: base.toolName } : {}),
  };
}

function fromTsStep(step, i, all, runKind) {
  const n = isNum(step.n) ? step.n : i + 1;
  const raw = String(step.name ?? "");
  if (step.type === "end_state" || raw.toLowerCase() === "end state") {
    const changes = Array.isArray(step.output?.changes)
      ? step.output.changes.length
      : 0;
    return laneStep(step, {
      n,
      kind: "end",
      name: `End state: ${changes} ${changes === 1 ? "change" : "changes"}`,
    });
  }
  if (step.type === "customer" || step.role === "customer")
    return laneStep(step, { n, kind: "customer", name: "customer" });
  const mcp = MCP_RE.exec(raw);
  const bare = mcp ? mcp[1] : raw;
  if (
    step.type === "tool_call" ||
    (step.type === "node" && step.node_type !== "llm")
  ) {
    const declared = step.type === "node" ? step.node_type : null;
    const kind = toolKind(bare, step.input, declared);
    return laneStep(step, {
      n,
      kind,
      name: bare,
      toolName: bare,
      mappedFrom: mcp || (runKind === "harness" && bare !== raw) ? raw : undefined, // prettier-ignore
    });
  }
  // LLM call or LLM node
  const lower = raw.toLowerCase();
  const isReply =
    step.role === "assistant" ||
    REPLY_NAMES.has(lower) ||
    (runKind === "prompt" &&
      typeof step.output === "string" &&
      i === lastLlmIndex(all));
  if (isReply) {
    const words = countWords(step.output);
    return laneStep(step, {
      n,
      kind: "reply",
      name: lower === "assistant" ? `assistant · ${words} words` : raw,
    });
  }
  return laneStep(step, { n, kind: "think", name: raw });
}

function lastLlmIndex(all) {
  for (let i = all.length - 1; i >= 0; i -= 1)
    if (
      all[i].type === "llm" ||
      (all[i].type === "node" && all[i].node_type === "llm")
    )
      return i;
  return -1;
}

function fromNormalized(step, i, all, runKind) {
  const n = isNum(step.n) ? step.n : isNum(step.order) ? step.order + 1 : i + 1;
  if (step.nkind === "end")
    return laneStep(step, { n, kind: "end", name: step.name });
  if (step.nkind === "customer")
    return laneStep(step, { n, kind: "customer", name: "customer" });
  if (step.nkind === "tool") {
    const bare = step.toolName ?? step.name;
    const kind = toolKind(
      bare,
      step.input,
      step.type === "retriever" ? "retriever" : null,
    );
    const raw = step.rawName ?? null;
    return laneStep(step, {
      n,
      kind,
      name: bare,
      toolName: bare,
      mappedFrom: raw && raw !== bare ? raw : undefined,
    });
  }
  const raw = String(step.name ?? "");
  const lower = raw.toLowerCase();
  const lastLlm = (() => {
    for (let k = all.length - 1; k >= 0; k -= 1)
      if (all[k].nkind === "llm") return k;
    return -1;
  })();
  const isReply =
    step.span_role === "assistant" ||
    REPLY_NAMES.has(lower) ||
    lower.startsWith("assistant") ||
    (runKind === "prompt" && i === lastLlm);
  return laneStep(step, {
    n,
    kind: isReply ? "reply" : "think",
    name: raw,
  });
}

/**
 * Normalise a trajectory to lane steps.
 * @param {object[]} steps  TS world steps (`{n, type, name, …}`), trace steps
 *   (alignSteps.flattenTrace output) or normalizeSteps output (`nkind`)
 * @param {{runKind?: "prompt"|"agent"|"code"|"harness"|string}} [opts]
 * @returns {object[]} lane steps
 */
export function toLaneSteps(steps, { runKind } = {}) {
  const list = (steps ?? []).filter(Boolean);
  if (!list.length) return [];
  if (list.every(isTsStep))
    return list.map((s, i) => fromTsStep(s, i, list, runKind));
  const normalized = list.some((s) => s.nkind)
    ? list
    : normalizeSteps(
        list.map((s) => ({ ...s, rawName: s.name })),
        { harness: runKind === "harness" },
      ).map((s) => ({ ...s, rawName: s.rawName }));
  return normalized.map((s, i) => fromNormalized(s, i, normalized, runKind));
}

/** Lane steps of a `GET /tracer/trace/{id}/` body (root excluded). */
export const laneStepsFromTrace = (trace, opts) =>
  toLaneSteps(flattenTrace(trace), opts);

// ---------------------------------------------------------------- align

/** Alignment key class: tools and retrievals align by tool name. */
const keyOf = (s) => {
  if (s.kind === "tool" || s.kind === "retrieval")
    return `act:${s.toolName ?? s.name}`;
  if (s.kind === "file_edit")
    return `file_edit:${s.input?.path ?? s.input?.file_path ?? ""}`;
  if (s.kind === "command" || s.kind === "test" || s.kind === "subagent")
    return s.kind;
  return s.kind; // reply · customer · end
};
const ARGS_KINDS = new Set([...ACTION_KINDS]);
const argsHash = (s) => (ARGS_KINDS.has(s.kind) ? stableJson(s.input) : "");

/** Milestones of a lane with their riding `think` steps (`subSteps`). */
export function milestonesOf(steps) {
  const out = [];
  let pending = [];
  (steps ?? []).forEach((s) => {
    if (!s.isMilestone) {
      pending.push(s);
      return;
    }
    out.push({ step: s, subSteps: pending, key: keyOf(s), args: argsHash(s) });
    pending = [];
  });
  // Trailing think steps (no milestone after them) ride with the last one.
  if (pending.length) {
    if (out.length) out[out.length - 1].trailing = pending;
    else out.push({ step: null, subSteps: pending, key: "think", args: "" });
  }
  const replies = out.filter((m) => m.step?.kind === "reply");
  if (replies.length) replies[replies.length - 1].finalReply = true;
  return out;
}

/** Weighted LCS: same key 1; + 1 for equal args (or final reply ↔ final reply). */
function weightedPairs(a, b) {
  const n = a.length;
  const m = b.length;
  const W = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  // IT-R2 G4 P7: the n-th customer (caller) turn pairs with the n-th one,
  // so matching turns never fold into `Only in …`.
  const ordinals = (xs) => {
    let c = 0;
    return xs.map((x) => (x.step?.kind === "customer" ? (c += 1) : 0));
  };
  const oa = ordinals(a);
  const ob = ordinals(b);
  const score = (i, j) => {
    if (!a[i].step || !b[j].step || a[i].key !== b[j].key) return 0;
    if (a[i].step.kind === "customer") return oa[i] === ob[j] ? 3 : 1;
    if (ARGS_KINDS.has(a[i].step.kind)) return a[i].args === b[j].args ? 2 : 1;
    if (a[i].step.kind === "reply")
      return a[i].finalReply && b[j].finalReply ? 2 : 1;
    return 1;
  };
  for (let i = n - 1; i >= 0; i -= 1)
    for (let j = m - 1; j >= 0; j -= 1) {
      const w = score(i, j);
      W[i][j] = Math.max(W[i + 1][j], W[i][j + 1], w ? w + W[i + 1][j + 1] : 0);
    }
  const pairs = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    const w = score(i, j);
    if (w && W[i][j] === w + W[i + 1][j + 1]) {
      pairs.push([i, j]);
      i += 1;
      j += 1;
    } else if (W[i][j + 1] >= W[i + 1][j]) j += 1;
    // On a tie skip the OTHER lane's step: the baseline's earliest
    // milestone pairs with the earliest equal step of the other lane.
    else i += 1;
  }
  return pairs;
}

const allStepsOf = (m) => [
  ...(m.subSteps ?? []),
  ...(m.step ? [m.step] : []),
  ...(m.trailing ?? []),
];

const PLURAL = {
  customer: ["customer turn", "customer turns"],
  reply: ["reply", "replies"],
  think: ["model step", "model steps"],
  tool: ["tool call", "tool calls"],
  file_read: ["file read", "file reads"],
  retrieval: ["retrieval", "retrievals"],
  file_edit: ["edit", "edits"],
  command: ["command", "commands"],
  test: ["test run", "test runs"],
  subagent: ["subagent", "subagents"],
  end: ["end state", "end states"],
};
const SUMMARY_ORDER = [
  "file_read",
  "file_edit",
  "test",
  "command",
  "tool",
  "retrieval",
  "subagent",
  "reply",
  "customer",
  "think",
  "end",
];

/** `9 file reads, 3 edits, 2 test runs` for a list of lane steps. */
export function gapSummary(steps) {
  const counts = {};
  (steps ?? []).forEach((s) => {
    const k =
      s.kind === "tool" &&
      READ_TOOLS.has(
        String(s.mappedFrom ?? s.name)
          .replace(MCP_RE, "$1")
          .toLowerCase(),
      )
        ? "file_read"
        : s.kind;
    counts[k] = (counts[k] ?? 0) + 1;
  });
  return SUMMARY_ORDER.filter((k) => counts[k])
    .map((k) => `${counts[k]} ${PLURAL[k][counts[k] === 1 ? 0 : 1]}`)
    .join(", ");
}

const gapCell = (milestones) => {
  const steps = milestones.flatMap(allStepsOf);
  return {
    type: "gap",
    count: steps.length,
    steps,
    milestones: milestones.map((m) => m.step).filter(Boolean),
    summary: gapSummary(steps),
  };
};
const stepCell = (m) => ({
  type: "step",
  step: m.step,
  subSteps: [...(m.subSteps ?? []), ...(m.trailing ?? [])],
});

function compareCells(base, other) {
  const a = base.step;
  const b = other.step;
  const inputChanged = ARGS_KINDS.has(a.kind) && !sameValue(a.input, b.input);
  const errorChanged = Boolean(a.error) !== Boolean(b.error);
  let outputChanged = false;
  if (!errorChanged) {
    if (a.kind === "end") outputChanged = a.name !== b.name;
    else outputChanged = !sameValue(a.output, b.output) || a.error !== b.error;
  }
  return { inputChanged, outputChanged, errorChanged };
}

const laneOf = (lane) => ({
  id: lane?.id ?? lane?.run_id ?? null,
  label: lane?.label ?? null,
  steps: lane?.steps ?? [],
});

/**
 * Align lanes against the baseline.
 * @param {{id: string, label: string, steps: object[]}} base  lane steps
 * @param {{id: string, label: string, steps: object[]}[]} others
 * @param {{mode?: "milestone"|"index"|"none"}} [opts]
 * @returns {{rows: object[], footer: {aligned: number, total: number,
 *   folded: number}, lanes: {id, label}[], mode: string}}
 *   row: `{id: "m3"|"g2", index: number|null (M-number), kind:
 *   "milestone"|"gap", key, cells: (cell|null)[] (base first), change:
 *   "same"|"changed"|"only"|"missing"|"none", inputChanged, outputChanged}`;
 *   cell: `{type: "step", step, subSteps, inputChanged?, outputChanged?,
 *   errorChanged?}` or `{type: "gap", count, steps, milestones, summary}`.
 */
export function alignLanes(base, others = [], { mode = "milestone" } = {}) {
  const b = laneOf(base);
  const os = (others ?? []).map(laneOf);
  const lanes = [b, ...os].map(({ id, label }) => ({ id, label }));
  const total = [b, ...os].reduce((sum, l) => sum + l.steps.length, 0);
  if (mode === "index" || mode === "none")
    return alignByIndex(b, os, lanes, total, mode);

  const bm = milestonesOf(b.steps);
  const B = bm.length;
  // per other lane: base index → other milestone, and unmatched slots
  const perLane = os.map((o) => {
    const om = milestonesOf(o.steps);
    const pairs = weightedPairs(bm, om);
    const matchOf = new Map(pairs.map(([i, j]) => [i, j]));
    const matchedOther = new Set(pairs.map(([, j]) => j));
    const slots = Array.from({ length: B + 1 }, () => []);
    om.forEach((m, j) => {
      if (matchedOther.has(j)) return;
      const next = pairs.find(([, pj]) => pj > j);
      slots[next ? next[0] : B].push(m);
    });
    return { om, matchOf, slots };
  });
  // unmatched base milestones (matched by no lane) fold too
  const baseMatched = (i) => perLane.some((p) => p.matchOf.has(i));

  const rows = [];
  let mNo = 0;
  let gNo = 0;
  let aligned = 0;
  const pushGap = (cells) => {
    gNo += 1;
    rows.push({
      id: `g${gNo}`,
      index: null,
      kind: "gap",
      key: "gap",
      cells,
      change: "only",
      inputChanged: false,
      outputChanged: false,
    });
  };
  let baseRun = []; // consecutive base-only milestones awaiting a fold
  const flushBase = () => {
    if (!baseRun.length) return;
    pushGap([gapCell(baseRun), ...os.map(() => null)]);
    baseRun = [];
  };
  for (let k = 0; k <= B; k += 1) {
    const before = perLane.map((p) => p.slots[k]);
    if (before.some((s) => s.length)) {
      flushBase();
      pushGap([null, ...before.map((s) => (s.length ? gapCell(s) : null))]);
    }
    if (k === B) break;
    const m = bm[k];
    if (!m.step) continue;
    if (os.length && !baseMatched(k)) {
      baseRun.push(m);
      continue;
    }
    flushBase();
    mNo += 1;
    const baseCell = stepCell(m);
    let inputChanged = false;
    let outputChanged = false;
    let anyMissing = false;
    let anyChanged = false;
    const cells = [baseCell];
    aligned += allStepsOf(m).length;
    perLane.forEach((p) => {
      const j = p.matchOf.get(k);
      if (j == null) {
        anyMissing = true;
        cells.push(null);
        return;
      }
      const om = p.om[j];
      const cell = stepCell(om);
      const diff = compareCells(baseCell, cell);
      Object.assign(cell, diff);
      inputChanged = inputChanged || diff.inputChanged;
      outputChanged = outputChanged || diff.outputChanged;
      anyChanged =
        anyChanged ||
        diff.inputChanged ||
        diff.outputChanged ||
        diff.errorChanged;
      aligned += allStepsOf(om).length;
      cells.push(cell);
    });
    rows.push({
      id: `m${mNo}`,
      index: mNo,
      kind: "milestone",
      key: m.key,
      cells,
      change: anyChanged ? "changed" : anyMissing ? "missing" : "same",
      inputChanged,
      outputChanged,
    });
  }
  flushBase();
  return {
    rows,
    footer: { aligned, total, folded: total - aligned },
    lanes,
    mode: "milestone",
  };
}

function alignByIndex(b, os, lanes, total, mode) {
  const len = Math.max(b.steps.length, ...os.map((o) => o.steps.length), 0);
  const rows = [];
  let aligned = 0;
  for (let i = 0; i < len; i += 1) {
    const bs = b.steps[i] ?? null;
    const cells = [bs ? { type: "step", step: bs, subSteps: [] } : null];
    let anyChanged = false;
    let anyMissing = false;
    let inputChanged = false;
    let outputChanged = false;
    os.forEach((o) => {
      const s = o.steps[i] ?? null;
      if (!s) {
        anyMissing = true;
        cells.push(null);
        return;
      }
      const cell = { type: "step", step: s, subSteps: [] };
      if (bs && mode === "index") {
        const same = bs.kind === s.kind && bs.name === s.name;
        const diff = same
          ? compareCells({ step: bs }, { step: s })
          : { inputChanged: false, outputChanged: true, errorChanged: false };
        Object.assign(cell, diff);
        inputChanged = inputChanged || diff.inputChanged;
        outputChanged = outputChanged || diff.outputChanged;
        anyChanged =
          anyChanged ||
          !same ||
          diff.inputChanged ||
          diff.outputChanged ||
          diff.errorChanged;
      }
      cells.push(cell);
    });
    aligned += cells.filter(Boolean).length;
    rows.push({
      id: `m${i + 1}`,
      index: i + 1,
      kind: "milestone",
      key: bs ? keyOf(bs) : "none",
      cells,
      change:
        mode === "none"
          ? "none"
          : !bs
            ? "only"
            : anyChanged
              ? "changed"
              : anyMissing
                ? "missing"
                : "same",
      inputChanged,
      outputChanged,
    });
  }
  return {
    rows,
    footer: { aligned: mode === "none" ? 0 : aligned, total, folded: 0 },
    lanes,
    mode,
  };
}

// --------------------------------------------------------------- markers

const MAX_PREVIEW = 42;
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

/** `order_id "8830" → "A-8830"` for two inputs (as the R2 banner wrote it). */
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

const isAction = (s) => s && ACTION_KINDS.includes(s.kind);

/** The base lane's `n` where a row sits (gap rows: the next base step). */
function baseStepNOf(rows, idx) {
  for (let i = idx; i < rows.length; i += 1) {
    const c = rows[i].cells[0];
    if (c?.type === "step") return c.step.n;
    if (c?.type === "gap" && c.steps.length) return c.steps[0].n;
  }
  for (let i = idx - 1; i >= 0; i -= 1) {
    const c = rows[i].cells[0];
    if (c?.type === "step") return c.step.n;
  }
  return null;
}

/** The first row whose ACTION differs (customer turns, replies and the end
 * state are context: they differ across kinds by design). */
function firstDifferenceOf(rows, lanes) {
  for (let r = 0; r < rows.length; r += 1) {
    const row = rows[r];
    if (row.kind === "gap") {
      for (let l = 0; l < row.cells.length; l += 1) {
        const cell = row.cells[l];
        const act = cell?.milestones?.find(isAction);
        if (act) {
          const who = lanes[l]?.label ?? "one run";
          return {
            rowId: row.id,
            milestoneIndex: null,
            baseStepN: baseStepNOf(rows, r),
            laneId: lanes[l]?.id ?? null,
            text: `${act.name} only in ${who}`,
          };
        }
      }
      continue;
    }
    const base = row.cells[0]?.step;
    if (!isAction(base)) continue;
    for (let l = 1; l < row.cells.length; l += 1) {
      const cell = row.cells[l];
      if (!cell) {
        return {
          rowId: row.id,
          milestoneIndex: row.index,
          baseStepN: base.n,
          laneId: lanes[l]?.id ?? null,
          text: `${base.name} only in ${lanes[0]?.label ?? "the baseline"}`,
        };
      }
      if (cell.inputChanged)
        return {
          rowId: row.id,
          milestoneIndex: row.index,
          baseStepN: base.n,
          laneId: lanes[l]?.id ?? null,
          text: `${base.name} input changed (${inputChangeText(base.input, cell.step.input)})`,
        };
      if (cell.errorChanged) {
        const failed = cell.step.error ? cell.step : base;
        const who = cell.step.error ? lanes[l]?.label : lanes[0]?.label;
        return {
          rowId: row.id,
          milestoneIndex: row.index,
          baseStepN: base.n,
          laneId: lanes[l]?.id ?? null,
          text: `${failed.name} failed in ${who} (${failed.error})`,
        };
      }
    }
  }
  return null;
}

const quotedTokens = (evidence) => {
  if (evidence == null) return [];
  if (typeof evidence === "object") {
    const v = evidence.value ?? evidence.token ?? null;
    return v == null ? [] : [String(v)];
  }
  return [...String(evidence).matchAll(/"([^"]+)"/g)].map((m) => m[1]);
};

/** The step a failed check points at, by the frozen fallback order:
 * cause_step_n → a quoted token of `evidence` first produced in a step's
 * output → the first errored step → the end state. */
function causeStepOf(steps, check) {
  if (isNum(check?.cause_step_n)) {
    const s = steps.find((x) => x.n === check.cause_step_n);
    if (s) return s;
  }
  for (const token of quotedTokens(check?.evidence)) {
    const needle = JSON.stringify(token);
    const s = steps.find(
      (x) => x.kind !== "end" && stableJson(x.output).includes(needle),
    );
    if (s) return s;
  }
  const errored = steps.find((x) => x.error);
  if (errored) return errored;
  return steps.find((x) => x.kind === "end") ?? steps[steps.length - 1] ?? null;
}

function rowOfStep(rows, laneIdx, n) {
  for (const row of rows) {
    const cell = row.cells[laneIdx];
    if (!cell) continue;
    const hit =
      cell.type === "step"
        ? cell.step?.n === n || cell.subSteps?.some((s) => s.n === n)
        : cell.steps?.some((s) => s.n === n);
    if (hit) return row;
  }
  return null;
}

/**
 * Divergence markers of an alignment.
 * @param {{rows: object[], lanes: {id, label, steps}[],
 *   checks?: Object<string, {key, passed, cause_step_n?, evidence?}[]>}} args
 *   `lanes` base first (the same order as the rows' cells), with their lane
 *   steps; `checks` per lane id (the trial's checks).
 * @returns {{firstWrongStep: {laneId, laneLabel, stepN, stepName,
 *   milestoneIndex, rowId, checkKey}|null, firstDifference: {milestoneIndex,
 *   baseStepN, text, rowId, laneId}|null}}
 */
export function markers({ rows = [], lanes = [], checks = {} } = {}) {
  const firstDifference = firstDifferenceOf(rows, lanes);
  let firstWrongStep = null;
  const failingIdx = lanes.findIndex((l) =>
    (checks?.[l.id] ?? []).some((c) => c.passed === false),
  );
  if (failingIdx >= 0) {
    const lane = lanes[failingIdx];
    const check = checks[lane.id].find((c) => c.passed === false);
    const step = causeStepOf(lane.steps ?? [], check);
    if (step) {
      const row = rowOfStep(rows, failingIdx, step.n);
      firstWrongStep = {
        laneId: lane.id,
        laneLabel: lane.label,
        stepN: step.n,
        stepName:
          step.kind === "reply" || step.kind === "think"
            ? step.name
            : step.toolName ?? step.name,
        milestoneIndex: row?.index ?? null,
        rowId: row?.id ?? null,
        checkKey: check.key,
      };
    }
  }
  return { firstWrongStep, firstDifference };
}

// ------------------------------------------------------------ copy (frozen)

/** `First wrong step: #2B step 1 · classify_intent` */
export const firstWrongStepText = (m) =>
  m ? `First wrong step: ${m.laneLabel} step ${m.stepN} · ${m.stepName}` : "";

/** `First difference: step 2 · lookup_order input changed (order_id "8830" → "A-8830")` */
export const firstDifferenceText = (m) =>
  m ? `First difference: step ${m.baseStepN} · ${m.text}` : "";

/** Tooltip for both: `Step numbers are #2B's own steps.` */
export const stepNumbersHelp = (baseLabel) =>
  `Step numbers are ${baseLabel}'s own steps.`;

/**
 * `6 of 21 steps match · 15 only in one run (folded)` from an alignLanes result (or its
 * rows; a bare rows array recounts from the cells).
 */
export function howAligned(result) {
  let footer = result?.footer;
  if (!footer) {
    const rows = Array.isArray(result) ? result : result?.rows ?? [];
    let aligned = 0;
    let folded = 0;
    rows.forEach((row) =>
      row.cells.forEach((c) => {
        if (!c) return;
        if (c.type === "gap") folded += c.count;
        else if (row.kind === "milestone")
          aligned += 1 + (c.subSteps?.length ?? 0);
      }),
    );
    footer = { aligned, folded, total: aligned + folded };
  }
  const { aligned, total, folded } = footer;
  // IT-R2 G4 P6: plain words (`Aligned … · 4 folded` read as jargon).
  return `${aligned} of ${total} ${total === 1 ? "step" : "steps"} match${folded ? ` · ${folded} only in one run (folded)` : ""}`;
}

/** The help line under `howAligned`. */
export const howAlignedHelp = () => HOW_ALIGNED_HELP;
