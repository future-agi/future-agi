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
    const entryEvals = item.entry?.eval_scores || [];
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

function finiteInterval(item) {
  const start = Date.parse(item?.span?.start_time);
  const end = Date.parse(item?.span?.end_time);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    return null;
  }
  return { start, end };
}

/**
 * Partition direct siblings into local execution groups.
 *
 * Overlapping siblings form one fork. A later non-overlapping group starts
 * only after every active branch in the prior group has ended, so the next
 * group is joined from each prior branch terminal. The calculation never
 * compares unrelated branches elsewhere in the trace.
 */
function localSiblingGroups(siblings) {
  const timed = siblings.map((item) => ({
    item,
    interval: finiteInterval(item),
  }));
  if (timed.some(({ interval }) => !interval)) return null;

  timed.sort(
    (a, b) =>
      a.interval.start - b.interval.start ||
      a.interval.end - b.interval.end ||
      String(a.item.span.id || "").localeCompare(String(b.item.span.id || "")),
  );

  const groups = [];
  let activeEnd = -Infinity;
  for (const entry of timed) {
    if (groups.length === 0 || entry.interval.start >= activeEnd) {
      groups.push([entry.item]);
      activeEnd = entry.interval.end;
      continue;
    }
    groups[groups.length - 1].push(entry.item);
    activeEnd = Math.max(activeEnd, entry.interval.end);
  }
  return groups;
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
        spanCount: 0,
        totalLatency: 0,
        totalTokens: 0,
        totalCost: 0,
        errorCount: 0,
        evals: [],
        annotations: [],
      };
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
    const entryEvals = item.entry?.eval_scores || [];
    const entryAnnotations = item.entry?.annotations || [];
    if (entryEvals.length) node.evals.push(...entryEvals);
    if (entryAnnotations.length) node.annotations.push(...entryAnnotations);
  }

  // Build execution edges independently inside each direct-sibling set. The
  // previous implementation assigned global time buckets and connected every
  // node in one bucket to every node in the next, inventing transitions across
  // unrelated branches of the trace.
  const edgeMap = {};
  const addEdge = (sourceItem, targetItem) => {
    if (!sourceItem || !targetItem) return;
    const source = spanGroupKey(sourceItem.span);
    const target = spanGroupKey(targetItem.span);
    const edgeKey = `${source}->${target}`;
    if (!edgeMap[edgeKey]) {
      edgeMap[edgeKey] = {
        source,
        target,
        transitionCount: 0,
        ...(source === target ? { isSelfLoop: true } : {}),
      };
    }
    edgeMap[edgeKey].transitionCount += 1;
  };

  const itemByEntry = new Map(flatSpans.map((item) => [item.entry, item]));
  const childrenOf = (item) =>
    (item?.entry?.children || [])
      .map((childEntry) => itemByEntry.get(childEntry))
      .filter(Boolean);

  // Return the terminal span(s) of each subtree while building its local
  // execution edges. Connecting the next sibling group from subtree terminals
  // is important: when `generation` contains an LLM child and `evaluation`
  // follows generation, the real transition is LLM -> evaluation, not a fork
  // generation -> {LLM, evaluation}.
  const processItem = (item) => {
    const children = childrenOf(item);
    if (children.length === 0) return [item];
    return processSiblingSet(children, item);
  };

  const processSiblingSet = (siblings, parentItem) => {
    const groups = localSiblingGroups(siblings);

    // Missing/malformed timing is not a license to invent execution order.
    // Fall back to the authoritative hierarchy for this local sibling set.
    if (!groups) {
      if (parentItem) siblings.forEach((child) => addEdge(parentItem, child));
      return siblings.flatMap((child) => processItem(child));
    }

    if (parentItem) {
      groups[0].forEach((child) => addEdge(parentItem, child));
    }

    let previousTerminals = [];
    groups.forEach((group, index) => {
      if (index > 0) {
        previousTerminals.forEach((source) => {
          group.forEach((target) => addEdge(source, target));
        });
      }
      previousTerminals = group.flatMap((child) => processItem(child));
    });
    return previousTerminals;
  };

  processSiblingSet(
    flatSpans.filter((item) => item.depth === 0),
    null,
  );

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
