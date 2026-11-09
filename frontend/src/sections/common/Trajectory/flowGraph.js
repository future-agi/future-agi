// IT-R1 G4 · step-flow graphs for traces that carry no node ids: harness
// transcripts (the coding harness Code, the coding harness …) and voice calls. Their spans all hang
// off one root, so the parent → child graph is a star of tool calls. The
// flow graph follows the steps in order instead: one node per step name
// (MCP tools by their bare name, `caller (simulated)` → `caller`), an edge
// per transition, Start → the first step, the last step → End. Bare names
// make the coding harness Code and the coding harness graphs line up, so the Diff overlay works.
//
// `withStepFlow(tree, run)` marks an IN-MEMORY copy of the span tree (a
// non-enumerable key; spans untouched, Observe unaffected); `graphOfTree`
// builds the flow graph for marked trees and buildTraceGraph for the rest.
// Same graph shape as buildTraceGraph (canonical AgentGraph data). Pure.
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import { toolDisplayParts } from "src/sections/common/Align/normalize";
import { flattenSpans, hasExplicitNodes, kindOfRun } from "./graphNodes";

const FLOW = "__stepFlow";
const attrsOf = (span) => span?.span_attributes ?? span?.eval_attributes ?? {};
const timeOf = (iso) => {
  const t = Date.parse(iso ?? "");
  return Number.isFinite(t) ? t : null;
};

/** `harness` · `voice` · null: which traces read as a step flow. */
export function stepFlowKind(spanTree, run) {
  if (!Array.isArray(spanTree) || !spanTree.length) return null;
  if (hasExplicitNodes(spanTree)) return null;
  const root = spanTree[0]?.observation_span ?? {};
  const attrs = attrsOf(root);
  const kind = kindOfRun(run);
  if (kind === "voice" || root.observation_type === "conversation")
    return "voice";
  if (kind === "harness" || "fi.harness" in attrs) {
    // Model calls only (gateway capture) has no steps worth a flow.
    const tools = flattenSpans(spanTree).some(
      ({ span, depth }) => depth > 0 && span.observation_type === "tool",
    );
    return tools ? "harness" : null;
  }
  // IT-R5 G4 P1: any other run with no declared graph (LangGraph without
  // node ids, prompt runs, coded agents) draws its steps in order as a
  // chain, never a fan of every child under the root. One step stays as is.
  const steps = flattenSpans(spanTree).filter(({ depth }) => depth > 0);
  return steps.length >= 2 ? "steps" : null;
}

/** A marked copy of `spanTree` when it reads as a step flow, else itself. */
export function withStepFlow(spanTree, run) {
  const kind = stepFlowKind(spanTree, run);
  if (!kind) return spanTree;
  const copy = [...spanTree];
  Object.defineProperty(copy, FLOW, { value: kind, enumerable: false });
  return copy;
}

/** True for a tree marked by withStepFlow. */
export const isStepFlow = (tree) => Boolean(tree?.[FLOW]);

const SIMULATED = /\s*\(simulated\)$/i;
const SKIP = new Set(["end state"]);

/** The node of one step span: `{id, name, subtitle}` or null (skipped). */
export function flowNodeOf(span) {
  const raw = String(span?.name ?? "").trim();
  if (!raw || SKIP.has(raw.toLowerCase())) return null;
  if (span.observation_type === "tool") {
    const p = toolDisplayParts(raw);
    return {
      id: p.name,
      name: p.name,
      subtitle: p.server ? `MCP ${p.server}` : null,
    };
  }
  const name = raw.replace(SIMULATED, "");
  return { id: name, name, subtitle: null };
}

const num = (v) => (Number.isFinite(v) && v >= 0 ? v : 0);

/** A raw span name as its flow node name (`mcp__x__lookup_order` → `lookup_order`). */
export const flowNameOf = (name) =>
  toolDisplayParts(String(name ?? "").replace(SIMULATED, "")).name;

/** The flow graph of a marked tree (buildTraceGraph shape). */
export function buildFlowGraph(spanTree) {
  const steps = flattenSpans(spanTree)
    .map((item, i) => ({ ...item, i }))
    .filter(({ depth }) => depth > 0)
    .sort((x, y) => {
      const a = timeOf(x.span.start_time);
      const b = timeOf(y.span.start_time);
      if (a == null || b == null || a === b) return x.i - y.i;
      return a - b;
    });
  const nodes = new Map();
  const edges = new Map();
  const nodeToSpanIds = {};
  const edge = (source, target, span) => {
    const key = `${source}->${target}`;
    const e = edges.get(key) ?? {
      source,
      target,
      transition_count: 0,
      _lat: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      is_self_loop: source === target,
    };
    e.transition_count += 1;
    e._lat += num(span?.latency_ms);
    e.total_tokens += num(span?.total_tokens);
    e.total_cost += num(span?.cost);
    if (span?.status === "ERROR") e.error_count += 1;
    edges.set(key, e);
  };
  let prev = "__start__";
  steps.forEach(({ span }) => {
    const n = flowNodeOf(span);
    if (!n) return;
    const node = nodes.get(n.id) ?? {
      id: n.id,
      name: n.name,
      type: span.observation_type || "unknown",
      span_count: 0,
      _lat: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      evals: [],
      annotations: [],
      ...(n.subtitle ? { _subtitle: n.subtitle } : {}),
    };
    node.span_count += 1;
    node._lat += num(span.latency_ms);
    node.total_tokens += num(span.total_tokens);
    node.total_cost += num(span.cost);
    if (span.status === "ERROR") node.error_count += 1;
    // IT-R5 G4 P1: the model chip (first model seen on this step).
    const model = span.model ?? attrsOf(span)?.["llm.model_name"] ?? null;
    if (model && !node._model) node._model = String(model);
    nodes.set(n.id, node);
    (nodeToSpanIds[n.id] = nodeToSpanIds[n.id] ?? []).push(span.id);
    edge(prev, n.id, span);
    prev = n.id;
  });
  if (!nodes.size)
    return { nodes: [], edges: [], path_edges: [], nodeToSpanIds: {} };
  edge(prev, "__end__", null);
  const sentinel = (id, name, type) => ({
    id,
    name,
    type,
    span_count: 0,
    avg_latency_ms: 0,
    total_tokens: 0,
    total_cost: 0,
    error_count: 0,
    trace_count: 1,
  });
  return {
    nodes: [
      sentinel("__start__", "Start", "start"),
      ...[...nodes.values()].map(({ _lat, ...n }) => ({
        ...n,
        avg_latency_ms: n.span_count ? Math.round(_lat / n.span_count) : 0,
      })),
      sentinel("__end__", "End", "end"),
    ],
    edges: [...edges.values()].map(({ _lat, ...e }) => ({
      ...e,
      avg_latency_ms: e.transition_count
        ? Math.round(_lat / e.transition_count)
        : 0,
    })),
    path_edges: [],
    nodeToSpanIds,
  };
}

/** The graph of any lane tree: the step flow for marked trees. */
export function graphOfTree(tree) {
  if (!Array.isArray(tree)) return null;
  return isStepFlow(tree) ? buildFlowGraph(tree) : buildTraceGraph(tree);
}

/**
 * A step-flow tree's quality line: `Step flow · drawn from the steps in
 * order` (the provider's own reason kept for `Why?`).
 */
export function flowQuality(quality, kind) {
  const why =
    kind === "voice"
      ? "a call records turns, not node ids"
      : kind === "steps"
        ? "this run declares no agent graph"
        : "harness transcripts don't carry node ids";
  return {
    ...(quality ?? {}),
    quality: quality?.quality ?? "parent_child",
    flow: true,
    // IT-R3 G4 P4: which kind drew it (harness lines say `Built from tool
    // calls (no declared graph)`).
    flowKind: kind ?? null,
    label: "Step flow",
    reason: "drawn from the steps in order",
    detail: `${why}${quality?.reason ? ` (${quality.reason})` : ""}. Each step name is one node; arrows follow the order the steps ran.`,
    text: "Step flow · drawn from the steps in order",
  };
}
