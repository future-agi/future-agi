// Graph nodes and graph quality for one trace (R4 lane T, R4-SPEC D11–D12).
//
// `withAgentGraphNodes(spanTree, run)` gives an Agent Playground run's trace
// explicit `graph.node.*` on an IN-MEMORY COPY: only spans whose name is a
// node of the run's graph get a node id, and each node's parent is the
// previous node in step order (so a repeated tool call reads as a loop).
// Spans that are not graph nodes (internal model calls, the run's root, the
// end state) get nothing, so buildTraceGraph never draws a node the trace
// doesn't contain. No stored trace is touched: Observe renders as before.
//
// `graphQualityFor(spanTree, run)` says how good the graph is, in words the
// legend prints (`Full graph · from your Agent Playground graph`). Pure.
import { entryOfConfig, graphQualityOf } from "../RunSources/catalog";

const spanOf = (entry) => entry?.observation_span ?? {};
const attrsOf = (span) => span?.span_attributes ?? span?.eval_attributes ?? {};
const timeOf = (iso) => {
  const t = Date.parse(iso ?? "");
  return Number.isFinite(t) ? t : null;
};

/** Every span in the tree, depth first, with its depth. */
export function flattenSpans(spanTree) {
  const out = [];
  const walk = (entries, depth) =>
    (entries ?? []).forEach((entry) => {
      out.push({ entry, span: spanOf(entry), depth });
      walk(entry?.children, depth + 1);
    });
  walk(spanTree, 0);
  return out;
}

/** True when any span carries an explicit `graph.node.id`. */
export function hasExplicitNodes(spanTree) {
  return flattenSpans(spanTree).some(({ span }) => {
    const a = attrsOf(span);
    return Boolean(a["graph.node.id"] || a.graph_node_id || a.graphNodeId);
  });
}

/** The run's kind (`agent`, `code`, `harness`, …) from a run or its config. */
export const kindOfRun = (run) => run?.kind ?? run?.config?.kind ?? null;

/** Node ids of an Agent Playground run's graph (empty when none). */
export function agentNodeIds(run) {
  const nodes = run?.config?.agent?.nodes ?? run?.agent?.nodes ?? [];
  return nodes.map((n) => n?.id).filter(Boolean);
}

/**
 * A copy of `spanTree` with `graph.node.id / parent_id / name` on the spans
 * that are nodes of the run's Agent Playground graph. Returns the input
 * unchanged (same reference) for any other run, or when the trace already
 * carries node ids.
 * @param {object[]} spanTree  `observation_spans`
 * @param {object} run  run with `kind` and `config.agent.nodes`
 */
export function withAgentGraphNodes(spanTree, run) {
  if (!Array.isArray(spanTree) || !spanTree.length) return spanTree;
  if (kindOfRun(run) !== "agent") return spanTree;
  const ids = new Set(agentNodeIds(run));
  if (!ids.size || hasExplicitNodes(spanTree)) return spanTree;

  // Step order: start time, then tree order (stable).
  const ordered = flattenSpans(spanTree)
    .map((item, i) => ({ ...item, i }))
    .filter(({ depth }) => depth > 0)
    .sort((x, y) => {
      const a = timeOf(x.span.start_time);
      const b = timeOf(y.span.start_time);
      if (a == null || b == null || a === b) return x.i - y.i;
      return a - b;
    });
  const assigned = new Map();
  let prev = null;
  ordered.forEach(({ span }) => {
    if (!span?.id || !ids.has(span.name)) return;
    assigned.set(span.id, { id: span.name, parent: prev });
    prev = span.name;
  });
  if (!assigned.size) return spanTree;

  const copy = (entries) =>
    (entries ?? []).map((entry) => {
      const span = spanOf(entry);
      const node = assigned.get(span.id);
      const next = node
        ? {
            ...span,
            span_attributes: {
              ...attrsOf(span),
              "graph.node.id": node.id,
              ...(node.parent ? { "graph.node.parent_id": node.parent } : {}),
              "graph.node.name": node.id,
              "fi.graph.source": "agent_playground",
            },
          }
        : span;
      return {
        ...entry,
        observation_span: next,
        children: copy(entry?.children),
      };
    });
  return copy(spanTree);
}

// ------------------------------------------------------------ quality

const LABELS = {
  full: "Full graph",
  parent_child: "Basic graph",
  model_calls: "Model calls only",
  unknown: "Unknown",
};

/** Root span attributes (capture, framework, harness). */
function rootAttrs(spanTree) {
  return attrsOf(spanOf(spanTree?.[0]));
}

/** `CrewAI 0.86` → `CrewAI` (version dropped). */
const bareName = (v) =>
  String(v ?? "")
    .replace(/\s+v?\d[\w.-]*$/i, "")
    .trim() || null;

/** The framework / harness name a run or trace names, or null. */
function sourceName(run, attrs) {
  const entry = entryOfConfig(run?.config ?? {});
  if (entry?.name) return entry.short ?? entry.name;
  return (
    bareName(attrs["fi.framework"]) ?? bareName(attrs["fi.harness"]) ?? null
  );
}

/** Capture steps of a harness run: transcript | final_only | null. */
function captureSteps(run, attrs) {
  const fromRun = run?.config?.harness?.capture;
  if (fromRun && "steps" in fromRun) return fromRun.steps ?? null;
  if ("fi.capture.steps" in attrs) return attrs["fi.capture.steps"] ?? null;
  return undefined;
}

const result = (quality, source, reason) => ({
  quality,
  label: LABELS[quality],
  source,
  reason,
  text: `${LABELS[quality]} · ${reason}`,
});

/**
 * How good the trace's graph is.
 * @param {object[]} spanTree  `observation_spans`
 * @param {object} [run]  run with `kind` / `config` (optional)
 * @returns {{quality: "full"|"parent_child"|"model_calls"|"unknown",
 *   label: string, source: string, reason: string, text: string}}
 */
export function graphQualityFor(spanTree, run) {
  const attrs = rootAttrs(spanTree);
  const kind = kindOfRun(run);
  const name = sourceName(run, attrs);
  const steps = captureSteps(run, attrs);
  const isHarness = kind === "harness" || "fi.harness" in attrs;

  if (isHarness && steps === "final_only")
    return result(
      "unknown",
      "final_only",
      "Only the final result was uploaded, so there are no steps to draw",
    );
  if (kind === "agent" && agentNodeIds(run).length)
    return result(
      "full",
      "agent_playground",
      "from your Agent Playground graph",
    );
  if (hasExplicitNodes(spanTree)) {
    const src = flattenSpans(spanTree)
      .map(({ span }) => attrsOf(span)["fi.graph.source"])
      .find(Boolean);
    if (src === "connect_sdk")
      return result("full", "connect_sdk", "node ids reported by your agent");
    if (src === "agent_playground")
      return result(
        "full",
        "agent_playground",
        "from your Agent Playground graph",
      );
    return result("full", "manual", "node ids set on your spans");
  }
  if (
    isHarness &&
    (steps === null ||
      attrs["fi.capture.source"] === "gateway" ||
      graphQualityOf(run?.config ?? {}) === "model_calls")
  )
    return result(
      "model_calls",
      "gateway",
      `${name ?? "These"} runs were captured through the gateway, so tool runs and sub-agents are not visible`,
    );

  const q = run?.config ? graphQualityOf(run.config) : null;
  if (q === "unknown" && !isHarness) {
    const entry = entryOfConfig(run?.config ?? {});
    if (entry?.badge === "otel_preview")
      return result(
        "unknown",
        "otel",
        `${name ?? "This framework"} emits its own OpenTelemetry; check the spans`,
      );
  }
  if (isHarness && steps === "transcript")
    return result(
      "parent_child",
      "transcript",
      `parent → child only: ${name ?? "harness"} transcripts don't carry node ids`,
    );
  if (kind === "prompt")
    return result(
      "parent_child",
      "prompt",
      "parent → child only: prompt runs don't carry node ids",
    );
  if (q === "unknown")
    return result(
      "unknown",
      "unknown",
      `${name ?? "This run"} doesn't say how its spans relate; check the spans`,
    );
  return result(
    "parent_child",
    "spans",
    name
      ? `parent → child only: ${name} spans don't carry node ids we have verified`
      : "parent → child only: these spans don't carry node ids",
  );
}

/** Capture label for a harness run (F3 locked names), or null. */
export function captureLabelFor(spanTree, run) {
  const attrs = rootAttrs(spanTree);
  const isHarness = kindOfRun(run) === "harness" || "fi.harness" in attrs;
  if (!isHarness) return null;
  const steps = captureSteps(run, attrs);
  if (steps === "final_only") return "Final result only (no transcript)";
  if (steps === "transcript") return "Full steps (harness transcript)";
  if (steps === null) return "Model calls only (gateway)";
  return null;
}
