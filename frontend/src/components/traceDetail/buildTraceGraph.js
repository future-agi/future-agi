/**
 * Build agent graph from a span tree.
 *
 * Two strategies:
 * 1. **Explicit**: If any span carries `graph.node.id` in its
 *    span_attributes, group by that ID and derive edges from
 *    `graph.node.parent_id`.
 * 2. **Inferred nodes**: Group spans by `(observation_type, name)` and derive
 *    edges only from the authoritative span-parent relation.
 *
 * Returns: { nodes: [...], edges: [...] } ready for AgentGraph/React Flow.
 */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getSpan(entry) {
  return entry?.observation_span || {};
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

function numericSpanMetric(value) {
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function createRecordedEdge(source, target) {
  return {
    source,
    target,
    transition_count: 0,
    _total_latency_ms: 0,
    total_tokens: 0,
    total_cost: 0,
    error_count: 0,
    trace_count: 1,
    is_self_loop: source === target,
  };
}

function addTargetSpanMetrics(edge, span) {
  edge.transition_count += 1;
  edge._total_latency_ms += numericSpanMetric(span?.latency_ms);
  edge.total_tokens += numericSpanMetric(span?.total_tokens);
  edge.total_cost += numericSpanMetric(span?.cost);
  if (span?.status === "ERROR") edge.error_count += 1;
}

function finalizeRecordedEdges(edges) {
  return edges.map(({ _total_latency_ms: latencyTotal, ...edge }) => ({
    ...edge,
    avg_latency_ms:
      edge.transition_count > 0
        ? Math.round(latencyTotal / edge.transition_count)
        : 0,
  }));
}

/** Collapse the recorded span-parent relation into graph-node edges. */
function buildRecordedHierarchyEdges(flatSpans, nodeIdForItem) {
  const nodeIdBySpanId = new Map();
  flatSpans.forEach((item) => {
    const spanId = item.span?.id;
    const nodeId = nodeIdForItem(item);
    if (spanId && nodeId) nodeIdBySpanId.set(spanId, nodeId);
  });

  const edgeMap = new Map();
  flatSpans.forEach((item) => {
    const source = nodeIdBySpanId.get(item.parentSpanId);
    const target = nodeIdForItem(item);
    if (!source || !target) return;

    const key = `${source}->${target}`;
    const edge = edgeMap.get(key) || createRecordedEdge(source, target);
    addTargetSpanMetrics(edge, item.span);
    edgeMap.set(key, edge);
  });

  return finalizeRecordedEdges(Array.from(edgeMap.values()));
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
        span_count: 0,
        _total_latency_ms: 0,
        total_tokens: 0,
        total_cost: 0,
        error_count: 0,
        trace_count: 1,
        evals: [],
        annotations: [],
      };
    }

    const node = nodeMap[nodeId];
    node.span_count += 1;
    if (!nodeToSpanIds[nodeId]) nodeToSpanIds[nodeId] = [];
    if (span.id) nodeToSpanIds[nodeId].push(span.id);
    node._total_latency_ms += span.latency_ms || 0;
    node.total_tokens += span.total_tokens || 0;
    node.total_cost += span.cost || 0;
    if (span.status === "ERROR") node.error_count += 1;
    if (
      item.entry?._filterMatch === true ||
      item.entry?._filterMatch === undefined
    ) {
      node._hasMatch = true;
    }
    // Collect evals and annotations
    const entryEvals = item.entry?.eval_scores || [];
    const entryAnnotations = item.entry?.annotations || [];
    if (entryEvals.length) node.evals.push(...entryEvals);
    if (entryAnnotations.length) node.annotations.push(...entryAnnotations);

    // Derive edge from graph.node.parent_id
    const parentNodeId = getGraphNodeParentId(span);
    if (parentNodeId && parentNodeId !== nodeId) {
      const edgeKey = `${parentNodeId}->${nodeId}`;
      if (!edgeMap[edgeKey]) {
        edgeMap[edgeKey] = createRecordedEdge(parentNodeId, nodeId);
      }
      addTargetSpanMetrics(edgeMap[edgeKey], span);
    } else if (parentNodeId === nodeId) {
      // Self-loop
      const edgeKey = `${nodeId}->${nodeId}`;
      if (!edgeMap[edgeKey]) {
        edgeMap[edgeKey] = createRecordedEdge(nodeId, nodeId);
      }
      addTargetSpanMetrics(edgeMap[edgeKey], span);
    }
  }

  // Compute averages
  const nodes = Object.values(nodeMap).map(
    ({ _total_latency_ms: latencyTotal, ...node }) => ({
      ...node,
      avg_latency_ms:
        node.span_count > 0 ? Math.round(latencyTotal / node.span_count) : 0,
    }),
  );

  return {
    nodes,
    edges: finalizeRecordedEdges(Object.values(edgeMap)),
    // graph.node.parent_id is topology, not chronological execution order.
    path_edges: [],
    nodeToSpanIds,
  };
}

// ---------------------------------------------------------------------------
// Strategy 2: Inferred nodes with recorded span hierarchy
// ---------------------------------------------------------------------------

/** Group key for a span: "type:name" */
function spanGroupKey(span) {
  const type = span.observation_type || "unknown";
  const name = span.name || "unnamed";
  return `${type}:${name}`;
}

function buildInferredGraph(flatSpans) {
  // Group by spanGroupKey, aggregating metrics
  const nodeMap = {}; // groupKey -> node data
  const nodeToSpanIds = {}; // groupKey -> [spanId1, spanId2, ...]

  for (const item of flatSpans) {
    const key = spanGroupKey(item.span);
    const type = item.span.observation_type || "unknown";
    const name = item.span.name || "unnamed";

    if (!nodeMap[key]) {
      nodeMap[key] = {
        id: key,
        name,
        type,
        span_count: 0,
        _total_latency_ms: 0,
        total_tokens: 0,
        total_cost: 0,
        error_count: 0,
        trace_count: 1,
        evals: [],
        annotations: [],
      };
    }

    const node = nodeMap[key];
    node.span_count += 1;
    if (!nodeToSpanIds[key]) nodeToSpanIds[key] = [];
    if (item.span.id) nodeToSpanIds[key].push(item.span.id);
    node._total_latency_ms += item.span.latency_ms || 0;
    node.total_tokens += item.span.total_tokens || 0;
    node.total_cost += item.span.cost || 0;
    if (item.span.status === "ERROR") node.error_count += 1;
    // Track if any span in this node group matched the filter
    if (
      item.entry?._filterMatch === true ||
      item.entry?._filterMatch === undefined
    ) {
      node._hasMatch = true;
    }
    const entryEvals = item.entry?.eval_scores || [];
    const entryAnnotations = item.entry?.annotations || [];
    if (entryEvals.length) node.evals.push(...entryEvals);
    if (entryAnnotations.length) node.annotations.push(...entryAnnotations);
  }

  // Compute averages
  const nodes = Object.values(nodeMap).map(
    ({ _total_latency_ms: latencyTotal, ...node }) => ({
      ...node,
      avg_latency_ms:
        node.span_count > 0 ? Math.round(latencyTotal / node.span_count) : 0,
    }),
  );

  return {
    nodes,
    edges: buildRecordedHierarchyEdges(flatSpans, (item) =>
      spanGroupKey(item.span),
    ),
    // Parent/child hierarchy plus timestamps is a partial order. It cannot
    // prove one chronological path through concurrent or sibling spans.
    path_edges: [],
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
    span_count: 0,
    avg_latency_ms: 0,
    total_tokens: 0,
    total_cost: 0,
    error_count: 0,
    trace_count: 1,
  };

  const endNode = {
    id: "__end__",
    name: "End",
    type: "end",
    span_count: 0,
    avg_latency_ms: 0,
    total_tokens: 0,
    total_cost: 0,
    error_count: 0,
    trace_count: 1,
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
      transition_count: 1,
      avg_latency_ms: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      is_self_loop: false,
    })),
    ...leafIds.map((n) => ({
      source: n.id,
      target: "__end__",
      transition_count: 1,
      avg_latency_ms: 0,
      total_tokens: 0,
      total_cost: 0,
      error_count: 0,
      trace_count: 1,
      is_self_loop: false,
    })),
  ];

  return {
    nodes: [startNode, ...graph.nodes, endNode],
    edges: [...graph.edges, ...newEdges],
    path_edges: graph.path_edges,
    nodeToSpanIds: graph.nodeToSpanIds || {},
  };
}

export function buildTraceGraph(spanTree) {
  if (!Array.isArray(spanTree)) {
    throw new Error("Trace graph requires a span-tree array");
  }
  if (spanTree.length === 0) {
    return { nodes: [], edges: [], path_edges: [], nodeToSpanIds: {} };
  }

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
