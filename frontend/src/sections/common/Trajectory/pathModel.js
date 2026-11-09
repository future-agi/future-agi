// Aggregate paths of a run (R4 lane T, R4-SPEC D14). Pure; shared by the
// mock handler (`GET /model-hub/r4/runs/{id}/paths/`) and the Paths screen.
//
// Node identity is stated, never assumed (`node_source`):
//   milestones   task suites: G0 milestones (customer turns dropped, every
//                reply collapses to `reply`, the end state is the End node)
//   graph_nodes  runs with a full graph: explicit `graph.node.id`
//   span_names   else: top-level step names (a renamed step is a new step)
// Consecutive repeats collapse to `{name, repeat}`; the path id is the
// joined sequence. Per run: attempts (scored tries only; tries that didn't
// run are excluded, errored = failed, as the Matrix counts them), per path
// attempts / failed / share / fail_share / example rows (≤ 6, failing
// first), and the aggregate graph (AgentGraph canonical data + AgentPath
// `path_edges`; `error_count` = failing attempts through the node / edge).
import { toLaneSteps } from "../Align/milestones";
import { fmtPct } from "../format/fmt";

export const NODE_SOURCES = Object.freeze([
  "milestones",
  "graph_nodes",
  "span_names",
]);

export const NODE_SOURCE_TEXT = Object.freeze({
  milestones: "Steps are this task suite's milestones",
  graph_nodes: "Steps are graph nodes",
  span_names:
    "Steps are top-level span names: a renamed step counts as a new step",
});

export const MAX_EXAMPLES = 6;
const REPLY = "reply";
const START = "__start__";
const END = "__end__";

const KIND_TYPE = {
  tool: "tool",
  retrieval: "retriever",
  reply: "llm",
  think: "llm",
  subagent: "agent",
  file_edit: "tool",
  command: "tool",
  test: "tool",
};

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const tokensOf = (s) =>
  isNum(s.tokens) ? s.tokens : isNum(s.tokens?.total) ? s.tokens.total : 0;
// W1-9 B4: the failing checks traceVerdicts placed on a step ride its visit.
const failingOf = (s) =>
  s?.failing_checks?.length ? { failing_checks: [...s.failing_checks] } : {};

// W1-9 review M3: a model turn is a `reply` milestone only when it answers.
// A turn that says nothing (the engine records no text for a turn that
// only calls tools) or only asks for tool calls is the model thinking: it
// rides along with the next action, as a designed trajectory's tool-calling
// turns do (they carry no `role`). So an engine try and a designed try that
// make the same calls take the same path. Errors stay what they are.
const isEmpty = (v) =>
  v == null ||
  (typeof v === "string" && !v.trim()) ||
  (Array.isArray(v) && !v.length);
const answers = (output) => {
  if (isEmpty(output)) return false;
  if (typeof output !== "object") return true;
  if (output.error) return true;
  if (Array.isArray(output.tool_calls) && output.tool_calls.length)
    return !isEmpty(output.content);
  return true;
};
const asThinking = (s) =>
  s?.type === "llm" && s.role === "assistant" && !answers(s.output)
    ? { ...s, role: undefined }
    : s;

/** Which node identity a run's paths use. */
export function nodeSourceFor({ isTaskSuite, graphQuality } = {}) {
  if (isTaskSuite) return "milestones";
  if (graphQuality === "full") return "graph_nodes";
  return "span_names";
}

/**
 * The visits of one attempt in order, before collapsing:
 * `[{name, type, ms, tokens, cost}]`.
 * @param {object[]} steps  TS world trajectory steps (or trace steps)
 * @param {{nodeSource: string, runKind?: string}} opts
 */
export function attemptVisits(steps, { nodeSource, runKind } = {}) {
  const list = (steps ?? []).filter(Boolean);
  if (nodeSource === "graph_nodes") {
    return list
      .map((s) => ({ s, id: s.attributes?.["graph.node.id"] }))
      .filter(({ id }) => Boolean(id))
      .map(({ s, id }) => ({
        name: id,
        type: s.node_type ?? (s.type === "tool_call" ? "tool" : "llm"),
        ms: isNum(s.ms) ? s.ms : 0,
        tokens: tokensOf(s),
        cost: isNum(s.cost) ? s.cost : 0,
        ...failingOf(s),
      }));
  }
  if (nodeSource === "span_names") {
    return list
      .filter(
        (s) =>
          !s.parent &&
          s.type !== "customer" &&
          s.role !== "customer" &&
          s.type !== "end_state",
      )
      .map((s) => ({
        name: s.span_name ?? s.name,
        type: s.node_type ?? (s.type === "tool_call" ? "tool" : "llm"),
        ms: isNum(s.ms) ? s.ms : 0,
        tokens: tokensOf(s),
        cost: isNum(s.cost) ? s.cost : 0,
        ...failingOf(s),
      }));
  }
  const lane = toLaneSteps(list.map(asThinking), { runKind });
  // Lane steps are one per step for trajectory steps; else matched by `n`.
  const srcOf = (s, i) =>
    lane.length === list.length ? list[i] : list.find((x) => x.n === s.n);
  return lane
    .map((s, i) => ({ s, src: srcOf(s, i) }))
    .filter(
      ({ s }) => s.isMilestone && s.kind !== "customer" && s.kind !== "end",
    )
    .map(({ s, src }) => ({
      name: s.kind === "reply" ? REPLY : s.name,
      type: KIND_TYPE[s.kind] ?? "tool",
      ms: isNum(s.ms) ? s.ms : 0,
      tokens: isNum(s.tokens) ? s.tokens : 0,
      cost: isNum(s.cost) ? s.cost : 0,
      ...failingOf(src),
    }));
}

/** Collapse consecutive repeats: `[{name, repeat}]`. */
export function collapse(visits) {
  const out = [];
  (visits ?? []).forEach((v) => {
    const last = out[out.length - 1];
    if (last && last.name === v.name) last.repeat += 1;
    else out.push({ name: v.name, repeat: 1 });
  });
  return out;
}

/** `classify_intent → lookup_order ×2 → reply` (also the path id). */
export const pathText = (steps) =>
  (steps ?? [])
    .map((s) => (s.repeat > 1 ? `${s.name} ×${s.repeat}` : s.name))
    .join(" → ");

const emptyNode = (id, name, type) => ({
  id,
  name,
  type,
  span_count: 0,
  avg_latency_ms: 0,
  total_tokens: 0,
  total_cost: 0,
  error_count: 0,
  trace_count: 0,
  failing_checks: {},
  _ms: 0,
});
const emptyEdge = (source, target) => ({
  source,
  target,
  transition_count: 0,
  avg_latency_ms: 0,
  total_tokens: 0,
  total_cost: 0,
  error_count: 0,
  trace_count: 0,
  is_self_loop: source === target,
});

/**
 * PathAggregate of one run (r4Shapes.js).
 * @param {{runId: string, nodeSource: string, attempts: {rowId: string,
 *   rowLabel?: string, trial: number, failed: boolean, visits: object[]}[]}} input
 *   `visits` from attemptVisits
 */
export function aggregatePaths({ runId, nodeSource, attempts = [] }) {
  const n = attempts.length;
  const failedTotal = attempts.filter((a) => a.failed).length;
  const endFailing = {};
  const byId = new Map();
  const labelOf = new Map();
  const nodes = new Map();
  const edges = new Map();
  const nodeOf = (name, type) => {
    if (!nodes.has(name)) nodes.set(name, emptyNode(name, name, type));
    return nodes.get(name);
  };
  const edgeOf = (s, t) => {
    const k = `${s}->${t}`;
    if (!edges.has(k)) edges.set(k, emptyEdge(s, t));
    return edges.get(k);
  };

  attempts.forEach((a) => {
    const steps = collapse(a.visits);
    const id = pathText(steps);
    if (!byId.has(id))
      byId.set(id, { id, steps, fails: steps.map(() => ({})), attempts: 0, failed: 0, rows: [] }); // prettier-ignore
    const p = byId.get(id);
    p.attempts += 1;
    if (a.failed) p.failed += 1;
    // per path step: tries whose check failed on it (repeats count once)
    const sets = steps.map(() => new Set());
    let at = -1;
    (a.visits ?? []).forEach((v, i, all) => {
      if (i === 0 || all[i - 1].name !== v.name) at += 1;
      (v.failing_checks ?? []).forEach((c) => sets[at].add(c));
    });
    sets.forEach((set, k) =>
      set.forEach((c) => (p.fails[k][c] = (p.fails[k][c] ?? 0) + 1)),
    );
    p.rows.push({ rowId: a.rowId, failed: a.failed });
    if (a.rowLabel && !labelOf.has(a.rowId)) labelOf.set(a.rowId, a.rowLabel);

    // nodes: visits, attempts through, failing attempts through
    const seenNodes = new Set();
    const failedAt = new Map();
    (a.visits ?? []).forEach((v) => {
      const node = nodeOf(v.name, v.type);
      node.span_count += 1;
      node._ms += v.ms;
      node.total_tokens += v.tokens;
      node.total_cost += v.cost;
      seenNodes.add(v.name);
      (v.failing_checks ?? []).forEach((c) =>
        failedAt.set(`${v.name}\u0000${c}`, [v.name, c]),
      );
    });
    // failing_checks: tries in which that check failed on the node (once
    // per try); the run's end (root / End state) counts on End.
    failedAt.forEach(([name, c]) => {
      const fc = nodes.get(name).failing_checks;
      fc[c] = (fc[c] ?? 0) + 1;
    });
    new Set(a.end_failing ?? []).forEach((c) => {
      endFailing[c] = (endFailing[c] ?? 0) + 1;
    });
    seenNodes.forEach((name) => {
      const node = nodes.get(name);
      node.trace_count += 1;
      if (a.failed) node.error_count += 1;
    });
    // edges: Start → … → End, self-loops for repeats
    const seq = [START, ...(a.visits ?? []).map((v) => v.name), END];
    const seenEdges = new Set();
    for (let i = 0; i < seq.length - 1; i += 1) {
      const e = edgeOf(seq[i], seq[i + 1]);
      e.transition_count += 1;
      seenEdges.add(`${seq[i]}->${seq[i + 1]}`);
    }
    seenEdges.forEach((k) => {
      const e = edges.get(k);
      e.trace_count += 1;
      if (a.failed) e.error_count += 1;
    });
  });

  const paths = [...byId.values()]
    .sort(
      (x, y) =>
        y.attempts - x.attempts ||
        y.failed - x.failed ||
        (x.id < y.id ? -1 : 1),
    )
    .map((p, i) => {
      const rows = [];
      [...p.rows.filter((r) => r.failed), ...p.rows.filter((r) => !r.failed)]
        .map((r) => r.rowId)
        .forEach((id) => !rows.includes(id) && rows.push(id));
      return {
        id: p.id,
        key: `p${i + 1}`,
        steps: p.steps.map((st, k) =>
          Object.keys(p.fails[k]).length
            ? { ...st, failing_checks: p.fails[k] }
            : st,
        ),
        attempts: p.attempts,
        failed: p.failed,
        share: n ? p.attempts / n : 0,
        fail_share: p.attempts ? p.failed / p.attempts : 0,
        example_rows: rows.slice(0, MAX_EXAMPLES),
        // additive: `T1`, `T11` … for the example rows (rowLabelOf upstream)
        example_labels: rows
          .slice(0, MAX_EXAMPLES)
          .map((id) => labelOf.get(id) ?? null),
        example_count: rows.length,
      };
    });

  const nodeList = [...nodes.values()].map(({ _ms, ...node }) => ({
    ...node,
    avg_latency_ms: node.span_count ? Math.round(_ms / node.span_count) : 0,
    total_cost: Math.round(node.total_cost * 1e6) / 1e6,
  }));
  const start = {
    ...emptyNode(START, "Start", "start"),
    trace_count: n,
    error_count: failedTotal,
  };
  const end = {
    ...emptyNode(END, "End", "end"),
    trace_count: n,
    error_count: failedTotal,
    failing_checks: endFailing,
  };
  delete start._ms;
  delete end._ms;
  const edgeList = [...edges.values()];
  return {
    run_id: runId,
    attempts: n,
    failed: failedTotal,
    node_source: nodeSource,
    paths,
    graph: {
      nodes: n ? [start, ...nodeList, end] : [],
      edges: n ? edgeList : [],
      path_edges: n ? edgeList : [],
    },
  };
}

// ------------------------------------------------------------ compare
// Path-distribution compare lives in pathCompare.js (re-exported here).
export { comparePaths } from "./pathCompare";

/**
 * Overlay graph data of two aggregates for runGraphDiff: canonical graphs
 * (start / end included) with `nodeToSpanIds` empty.
 */
export const aggregateGraph = (agg) => ({
  nodes: agg?.graph?.nodes ?? [],
  edges: agg?.graph?.edges ?? [],
  path_edges: agg?.graph?.path_edges ?? [],
  nodeToSpanIds: {},
});

// ------------------------------------------------------------ sentence

/** The path the sentence features: most failures, then most tries. */
export function featuredPath(agg) {
  const paths = agg?.paths ?? [];
  if (!paths.length) return null;
  return [...paths].sort(
    (x, y) => y.failed - x.failed || y.attempts - x.attempts,
  )[0];
}

/**
 * D14 sentence. No digit literals: every number comes from the aggregate.
 * `#2B: 20% of tries (18 of 90) take a → b ×2 → reply; all 18 fail.
 * #3A never takes that path.`
 * @returns {{text: string, path: object}|null}
 */
export function pathSentence(agg, { label, compare, compareLabel } = {}) {
  const p = featuredPath(agg);
  if (!p) return null;
  const pct = (v) => fmtPct(v, { dp: 0 });
  const head = `${label}: ${pct(p.share)} of tries (${p.attempts} of ${agg.attempts}) take ${pathText(p.steps)}`;
  let fail;
  if (p.failed === p.attempts) fail = `all ${p.failed} fail`;
  else if (p.failed === 0) fail = "none of those fail";
  else
    fail = `${pct(p.fail_share)} of those fail (${p.failed} of ${p.attempts})`;
  let tail = "";
  if (compare && compareLabel) {
    const other = (compare.paths ?? []).find((x) => x.id === p.id);
    tail = other
      ? ` ${compareLabel} takes it on ${pct(other.share)} (${other.attempts} of ${compare.attempts}).`
      : ` ${compareLabel} never takes that path.`;
  }
  return { text: `${head}; ${fail}.${tail}`, path: p };
}

/** `Every try takes the same path: …` when a run has one path only. */
export const onePathText = (agg) =>
  agg?.paths?.length === 1
    ? `Every try takes the same path: ${pathText(agg.paths[0].steps)}`
    : null;
