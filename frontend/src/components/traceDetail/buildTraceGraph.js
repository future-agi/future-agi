/**
 * Build agent graph from a span tree.
 *
 * Two strategies:
 * 1. **Explicit**: If any span carries `graph.node.id` in its
 *    span_attributes, group by that ID and derive edges from
 *    `graph.node.parent_id`.
 * 2. **Inferred**: Group spans by `(observation_type, name)`,
 *    assign steps via timing overlap analysis, connect consecutive steps.
 *
 * Returns: { nodes: [...], edges: [...] } ready for AgentGraph/React Flow.
 */

import { spanOwnEvalRows } from "./evalScores";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getSpan(entry) {
  return entry?.observation_span || {};
}

/** This span's own evals as graph-node rows (snake_case for node.evals). */
function flattenEntryEvals(entry) {
  return spanOwnEvalRows(entry).map((r) => ({
    eval_name: r.evalName,
    eval_config_id: r.evalConfigId,
    output_type: r.outputType,
    eval_task_name: r.taskName,
    score: r.score,
    result: r.result,
    error: r.error,
  }));
}

/** Flatten span tree into a list of { span, entry, depth, parentSpanId } */
function flattenTree(entries, depth = 0, parentSpanId = null) {
  const result = [];
  if (!entries) return result;
  for (const entry of entries) {
    const span = getSpan(entry);
    result.push({ span, entry, depth, parentSpanId });
    if (entry.children?.length) {
      result.push(...flattenTree(entry.children, depth + 1, span.id));
    }
  }
  return result;
}

/** Get graph.node.id from span attributes (supports multiple key formats) */
function getGraphNodeId(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  return (
    attrs["graph.node.id"] ||
    attrs["graph_node_id"] ||
    attrs["graphNodeId"] ||
    null
  );
}

/** Get graph.node.parent_id from span attributes */
function getGraphNodeParentId(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  return (
    attrs["graph.node.parent_id"] ||
    attrs["graph_node_parent_id"] ||
    attrs["graphNodeParentId"] ||
    null
  );
}

/** Get graph.node.name (display name) from span attributes */
function getGraphNodeName(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  return (
    attrs["graph.node.name"] ||
    attrs["graph.node.display_name"] ||
    attrs["graph_node_name"] ||
    null
  );
}

// ---------------------------------------------------------------------------
// Strategy 1: Explicit graph attributes
// ---------------------------------------------------------------------------

function buildExplicitGraph(flatSpans) {
  const nodeMap = {}; // graphNodeId -> node data
  const edgeMap = {}; // "source->target" -> { source, target, count }
  const nodeToSpanIds = {}; // graphNodeId -> [spanId1, spanId2, ...]

  for (const item of flatSpans) {
    const { span } = item;
    const nodeId = getGraphNodeId(span);
    if (!nodeId) continue;

    const displayName = getGraphNodeName(span) || span.name || nodeId;
    const type = span.observation_type || "unknown";

    if (!nodeMap[nodeId]) {
      nodeMap[nodeId] = {
        id: nodeId,
        name: displayName,
        type,
        spanCount: 0,
        totalLatency: 0,
        totalTokens: 0,
        totalCost: 0,
        errorCount: 0,
        evals: [],
        annotations: [],
      };
    }

    const node = nodeMap[nodeId];
    node.spanCount += 1;
    if (!nodeToSpanIds[nodeId]) nodeToSpanIds[nodeId] = [];
    if (span.id) nodeToSpanIds[nodeId].push(span.id);
    node.totalLatency += span.latency_ms || 0;
    node.totalTokens += span.total_tokens || 0;
    node.totalCost += span.cost || 0;
    if (span.status === "ERROR") node.errorCount += 1;
    if (
      item.entry?._filterMatch === true ||
      item.entry?._filterMatch === undefined
    ) {
      node._hasMatch = true;
    }
    // Collect evals and annotations
    const entryEvals = flattenEntryEvals(item.entry);
    const entryAnnotations = item.entry?.annotations || [];
    if (entryEvals.length) node.evals.push(...entryEvals);
    if (entryAnnotations.length) node.annotations.push(...entryAnnotations);

    // Derive edge from graph.node.parent_id
    const parentNodeId = getGraphNodeParentId(span);
    if (parentNodeId && parentNodeId !== nodeId) {
      const edgeKey = `${parentNodeId}->${nodeId}`;
      if (!edgeMap[edgeKey]) {
        edgeMap[edgeKey] = {
          source: parentNodeId,
          target: nodeId,
          transitionCount: 0,
        };
      }
      edgeMap[edgeKey].transitionCount += 1;
    } else if (parentNodeId === nodeId) {
      // Self-loop
      const edgeKey = `${nodeId}->${nodeId}`;
      if (!edgeMap[edgeKey]) {
        edgeMap[edgeKey] = {
          source: nodeId,
          target: nodeId,
          transitionCount: 0,
          isSelfLoop: true,
        };
      }
      edgeMap[edgeKey].transitionCount += 1;
    }
  }

  // Compute averages
  const nodes = Object.values(nodeMap).map((n) => ({
    ...n,
    avgLatencyMs:
      n.spanCount > 0 ? Math.round(n.totalLatency / n.spanCount) : 0,
  }));

  return {
    nodes,
    edges: Object.values(edgeMap),
    nodeToSpanIds,
  };
}

// ---------------------------------------------------------------------------
// Strategy 2: Timing-based inference
// ---------------------------------------------------------------------------

/** Group key for a span: "type:name" */
function spanGroupKey(span) {
  const type = span.observation_type || "unknown";
  const name = span.name || "unnamed";
  return `${type}:${name}`;
}

/**
 * Assign step numbers using timing overlap analysis.
 * Spans that overlap in time → same step (parallel).
 * Sequential spans → consecutive steps.
 */
function assignSteps(flatSpans) {
  if (flatSpans.length === 0) return [];

  // Sort by start time
  const sorted = [...flatSpans].sort((a, b) => {
    const aTime = new Date(a.span.start_time || 0).getTime();
    const bTime = new Date(b.span.start_time || 0).getTime();
    return aTime - bTime;
  });

  const result = [];
  let currentStep = 0;
  let currentGroupEnd = 0;

  for (const item of sorted) {
    const startMs = new Date(item.span.start_time || 0).getTime();
    const endMs = new Date(item.span.end_time || startMs).getTime();

    if (startMs >= currentGroupEnd && result.length > 0) {
      // New step — this span starts after the previous group ended
      currentStep++;
    }

    result.push({ ...item, step: currentStep });
    currentGroupEnd = Math.max(currentGroupEnd, endMs);
  }

  // Enforce parent-child constraints: child step must be > parent step
  const spanSteps = {};
  for (const item of result) {
    spanSteps[item.span.id] = item.step;
  }

  let changed = true;
  let iterations = 0;
  while (changed && iterations < 500) {
    changed = false;
    iterations++;
    for (const item of result) {
      if (item.parentSpanId && spanSteps[item.parentSpanId] !== undefined) {
        const parentStep = spanSteps[item.parentSpanId];
        if (item.step <= parentStep) {
          item.step = parentStep + 1;
          spanSteps[item.span.id] = item.step;
          changed = true;
        }
      }
    }
  }

  return result;
}

function buildInferredGraph(flatSpans) {
  const steppedSpans = assignSteps(flatSpans);

  // Group by spanGroupKey, aggregating metrics
  const nodeMap = {}; // groupKey -> node data
  const nodeSteps = {}; // groupKey -> Set of steps this node appears at
  const nodeToSpanIds = {}; // groupKey -> [spanId1, spanId2, ...]

  for (const item of steppedSpans) {
    const key = spanGroupKey(item.span);
    const type = item.span.observation_type || "unknown";
    const name = item.span.name || "unnamed";

    if (!nodeMap[key]) {
      nodeMap[key] = {
        id: key,
        name,
        type,
        spanCount: 0,
        totalLatency: 0,
        totalTokens: 0,
        totalCost: 0,
        errorCount: 0,
        evals: [],
        annotations: [],
      };
      nodeSteps[key] = new Set();
    }

    const node = nodeMap[key];
    node.spanCount += 1;
    if (!nodeToSpanIds[key]) nodeToSpanIds[key] = [];
    if (item.span.id) nodeToSpanIds[key].push(item.span.id);
    node.totalLatency += item.span.latency_ms || 0;
    node.totalTokens += item.span.total_tokens || 0;
    node.totalCost += item.span.cost || 0;
    if (item.span.status === "ERROR") node.errorCount += 1;
    // Track if any span in this node group matched the filter
    if (
      item.entry?._filterMatch === true ||
      item.entry?._filterMatch === undefined
    ) {
      node._hasMatch = true;
    }
    const entryEvals = flattenEntryEvals(item.entry);
    const entryAnnotations = item.entry?.annotations || [];
    if (entryEvals.length) node.evals.push(...entryEvals);
    if (entryAnnotations.length) node.annotations.push(...entryAnnotations);
    nodeSteps[key].add(item.step);
  }

  // Build edges from parent→child relationships (grouped)
  const edgeMap = {};
  for (const item of steppedSpans) {
    if (!item.parentSpanId) continue;

    const childKey = spanGroupKey(item.span);
    // Find parent span to get its group key
    const parentItem = steppedSpans.find(
      (s) => s.span.id === item.parentSpanId,
    );
    if (!parentItem) continue;

    const parentKey = spanGroupKey(parentItem.span);
    if (parentKey === childKey) {
      // Self-loop
      const edgeKey = `${childKey}->${childKey}`;
      if (!edgeMap[edgeKey]) {
        edgeMap[edgeKey] = {
          source: childKey,
          target: childKey,
          transitionCount: 0,
          isSelfLoop: true,
        };
      }
      edgeMap[edgeKey].transitionCount += 1;
    } else {
      const edgeKey = `${parentKey}->${childKey}`;
      if (!edgeMap[edgeKey]) {
        edgeMap[edgeKey] = {
          source: parentKey,
          target: childKey,
          transitionCount: 0,
        };
      }
      edgeMap[edgeKey].transitionCount += 1;
    }
  }

  // Also add step-based sequential edges for nodes without parent-child edges
  const stepToNodes = {};
  for (const item of steppedSpans) {
    const key = spanGroupKey(item.span);
    if (!stepToNodes[item.step]) stepToNodes[item.step] = new Set();
    stepToNodes[item.step].add(key);
  }

  const stepNumbers = Object.keys(stepToNodes)
    .map(Number)
    .sort((a, b) => a - b);
  for (let i = 0; i < stepNumbers.length - 1; i++) {
    const currentNodes = stepToNodes[stepNumbers[i]];
    const nextNodes = stepToNodes[stepNumbers[i + 1]];
    for (const src of currentNodes) {
      for (const tgt of nextNodes) {
        if (src === tgt) continue; // skip self-loops from step edges
        const edgeKey = `${src}->${tgt}`;
        if (!edgeMap[edgeKey]) {
          edgeMap[edgeKey] = { source: src, target: tgt, transitionCount: 1 };
        }
      }
    }
  }

  // Compute averages
  const nodes = Object.values(nodeMap).map((n) => ({
    ...n,
    avgLatencyMs:
      n.spanCount > 0 ? Math.round(n.totalLatency / n.spanCount) : 0,
  }));

  return {
    nodes,
    edges: Object.values(edgeMap),
    nodeToSpanIds,
  };
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/**
 * Build agent graph from span tree.
 *
 * @param {Array} spanTree — The span tree from the trace detail API
 *   (each entry: { observation_span: {...}, children: [...] })
 * @returns {{ nodes: Array, edges: Array }} — Graph data for AgentGraph
 */
/**
 * Add Start/End sentinel nodes and connect them to root/leaf nodes.
 */
function addSentinels(graph) {
  if (!graph.nodes.length) return graph;

  const startNode = {
    id: "__start__",
    name: "Start",
    type: "start",
    spanCount: 0,
    avgLatencyMs: 0,
    totalTokens: 0,
    totalCost: 0,
    errorCount: 0,
  };

  const endNode = {
    id: "__end__",
    name: "End",
    type: "end",
    spanCount: 0,
    avgLatencyMs: 0,
    totalTokens: 0,
    totalCost: 0,
    errorCount: 0,
  };

  // Find root nodes (never appear as edge target)
  const targets = new Set(graph.edges.map((e) => e.target));
  const roots = graph.nodes.filter((n) => !targets.has(n.id));

  // Find leaf nodes (never appear as edge source)
  const sources = new Set(graph.edges.map((e) => e.source));
  const leaves = graph.nodes.filter((n) => !sources.has(n.id));

  // If no roots found (all nodes are in cycles), connect Start to the first node
  const rootIds = roots.length > 0 ? roots : [graph.nodes[0]];
  const leafIds =
    leaves.length > 0 ? leaves : [graph.nodes[graph.nodes.length - 1]];

  const newEdges = [
    ...rootIds.map((n) => ({
      source: "__start__",
      target: n.id,
      transitionCount: 1,
    })),
    ...leafIds.map((n) => ({
      source: n.id,
      target: "__end__",
      transitionCount: 1,
    })),
  ];

  return {
    nodes: [startNode, ...graph.nodes, endNode],
    edges: [...graph.edges, ...newEdges],
    nodeToSpanIds: graph.nodeToSpanIds || {},
  };
}

export function buildTraceGraph(spanTree) {
  if (!spanTree?.length) return { nodes: [], edges: [] };

  const flatSpans = flattenTree(spanTree);

  // Check if any span has explicit graph.node.id attributes
  const hasExplicitGraph = flatSpans.some((item) => getGraphNodeId(item.span));

  let graph;
  if (hasExplicitGraph) {
    graph = buildExplicitGraph(flatSpans);
  } else {
    graph = buildInferredGraph(flatSpans);
  }

  // Add Start/End sentinel nodes
  graph = addSentinels(graph);

  return graph;
}
