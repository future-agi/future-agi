// Which trajectory view to open first (R4 implementation, R4-SPEC D12). Pure.
//
// Graph is recommended when the run has structure Steps can't show:
//   (a) ≥ 2 agents (distinct agent span names below the root),
//   (b) only with a full graph: a node that branches to ≥ 2 distinct nodes,
//   (c) a retry: the same tool or retrieval runs twice in a row at the
//       milestone level (internal model steps ride along, as in Paths),
//   (d) transfer spans.
// Model-calls-only traces never get (a) or (b): the gateway can't see
// agents or nodes. Everything else opens on Steps.
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import { toLaneSteps } from "../Align/milestones";
import { flattenTrace } from "../Align/normalize";
import { flattenSpans } from "./graphNodes";

const SENTINELS = new Set(["__start__", "__end__"]);
const RETRY_KINDS = new Set(["tool", "retrieval"]);
const HANDOFF_RE = /^(handoff|transfer)[\s_]/i;
const typeOf = (span) => String(span?.observation_type ?? "").toLowerCase();

/** Distinct agent names below the root (the root is the run itself). */
export function agentNames(spanTree) {
  const names = [];
  flattenSpans(spanTree).forEach(({ span, depth }) => {
    if (depth === 0 || typeOf(span) !== "agent") return;
    if (span.name && !names.includes(span.name)) names.push(span.name);
  });
  return names;
}

/** The first node of a full graph with ≥ 2 distinct targets, or null. */
export function branchNode(spanTree) {
  let graph;
  try {
    graph = buildTraceGraph(spanTree);
  } catch {
    return null;
  }
  const targets = new Map();
  (graph.edges ?? []).forEach((e) => {
    if (e.is_self_loop || SENTINELS.has(e.source) || SENTINELS.has(e.target))
      return;
    if (!targets.has(e.source)) targets.set(e.source, new Set());
    targets.get(e.source).add(e.target);
  });
  const hit = (graph.nodes ?? []).find(
    (n) => (targets.get(n.id)?.size ?? 0) >= 2,
  );
  return hit ? hit.name : null;
}

/**
 * Retries at the milestone level: `[{name, times, spanIds}]` for every run
 * of the same tool / retrieval twice or more in a row (think steps between
 * them ride along, as in G0's milestones).
 */
export function retriesOf(spanTree) {
  const flat = flattenTrace({ observation_spans: spanTree });
  const milestones = toLaneSteps(flat)
    .map((s, i) => ({ ...s, spanId: flat[i]?.id ?? null }))
    .filter((s) => s.isMilestone);
  const out = [];
  let i = 0;
  while (i < milestones.length) {
    const s = milestones[i];
    let j = i + 1;
    while (
      j < milestones.length &&
      RETRY_KINDS.has(s.kind) &&
      milestones[j].kind === s.kind &&
      milestones[j].name === s.name
    )
      j += 1;
    if (j - i >= 2)
      out.push({
        name: s.name,
        times: j - i,
        spanIds: milestones.slice(i, j).map((m) => m.spanId),
      });
    i = j;
  }
  return out;
}

const hasHandoff = (spanTree) =>
  flattenSpans(spanTree).some(
    ({ span, depth }) =>
      depth > 0 &&
      (HANDOFF_RE.test(span.name ?? "") ||
        typeOf(span) === "handoff" ||
        Boolean(span.span_attributes?.["handoff.to"])),
  );

/**
 * @param {object[]} spanTree  `observation_spans` (with graph nodes applied)
 * @param {{quality: string}|string} quality  graphQualityFor result or value
 * @param {{subject?: string}} [opts]  `this run` by default; Task compare
 *   passes the lane's name (`Support Agent v3`)
 * @returns {{view: "graph"|"steps", rule: "a"|"b"|"c"|"d"|null,
 *   reason: string|null}}  reason without the `Graph recommended · ` prefix
 */
export function recommendView(spanTree, quality, { subject } = {}) {
  const q = typeof quality === "string" ? quality : quality?.quality;
  const who = subject ?? "this run";
  if (!Array.isArray(spanTree) || !spanTree.length)
    return { view: "steps", rule: null, reason: null };
  const modelCallsOnly = q === "model_calls";
  if (!modelCallsOnly) {
    const agents = agentNames(spanTree);
    if (agents.length >= 2)
      return {
        view: "graph",
        rule: "a",
        reason: `${who} uses ${agents.length} agents`,
      };
    if (q === "full") {
      const node = branchNode(spanTree);
      if (node)
        return {
          view: "graph",
          rule: "b",
          reason: `${who} branches at ${node}`,
        };
    }
  }
  const retry = retriesOf(spanTree)[0];
  if (retry)
    return {
      view: "graph",
      rule: "c",
      reason: `${who} retried ${retry.name} ${retry.times}×`,
    };
  if (!modelCallsOnly && hasHandoff(spanTree))
    return {
      view: "graph",
      rule: "d",
      reason: `${who} hands off between agents`,
    };
  return { view: "steps", rule: null, reason: null };
}

/** `Graph recommended · this run retried lookup_order 2×`. */
export const recommendText = (rec) =>
  rec?.view === "graph" && rec.reason
    ? `Graph recommended · ${rec.reason}`
    : null;
