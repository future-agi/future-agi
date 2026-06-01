/**
 * buildGraphDiff — diff two AgentGraphs (from buildTraceGraph) and produce
 * a richer failing-side graph that surfaces, in-place:
 *
 *   1. What the failing trace actually did            (its real nodes/edges)
 *   2. What it *should* have done but skipped         (ghost nodes + dashed
 *                                                      "SKIPPED PATH" edges)
 *   3. Where things actually went wrong               (`_isFailurePoint` on
 *                                                      nodes whose spans had
 *                                                      errors)
 *
 * The shape we're given:
 *   buildTraceGraph returns FLAT nodes — `{ id, name, type, errorCount,
 *   avgLatencyMs, ... }` — NOT nested under `data`. AgentGraph itself wraps
 *   each node into `{ id, data: { ...node, _direction }, position }` for
 *   React Flow. So when we attach `_diffStatus` / `_isFailurePoint` at the
 *   top level of the node, those fields flow into `data._diffStatus` /
 *   `data._isFailurePoint` automatically — which is what AgentNode reads.
 *
 * Match key:
 *   `(type, name)` — both come from span attributes and are stable
 *   identifiers across traces of the same agent.
 *
 * Status labels (written as `node._diffStatus`):
 *   "fail-only"          → extra step in failing trace
 *   "pass-only-ghost"    → missing step ghosted INTO failing (was skipped)
 *   "pass-only"          → step missing from failing (annotated on pass side)
 *   "matched-regressed"  → exists both sides, but failing has errors and/or
 *                          is ≥1.5× slower
 *   "matched"            → exists both sides, comparable metrics
 *
 * The original graphs are NOT mutated. The pass-side graph carries the
 * standard diff annotations only; the storytelling cues (ghosts, skipped
 * edges, failure markers) live on the fail-side graph.
 */

const SLOWER_RATIO = 1.5;
const SENTINEL_TYPES = new Set(["start", "end"]);
const GHOST_PREFIX = "ghost-";

function keyOf(node) {
  const type = String(node?.type ?? "").toLowerCase().trim();
  const name = String(node?.name ?? "").toLowerCase().trim();
  return `${type}|${name}`;
}

// Tolerate both camelCase (buildTraceGraph output) and snake_case (older
// shapes / AgentNode's tooltip code). Whichever is populated, we read.
function errorCountOf(node) {
  return Number(node?.errorCount ?? node?.error_count ?? 0) || 0;
}
function avgLatencyOf(node) {
  return Number(node?.avgLatencyMs ?? node?.avg_latency_ms ?? 0) || 0;
}

function isRegressed(failNode, passNode) {
  const failErr = errorCountOf(failNode);
  const passErr = errorCountOf(passNode);
  if (failErr > 0 && passErr === 0) return true;

  const failLat = avgLatencyOf(failNode);
  const passLat = avgLatencyOf(passNode);
  if (passLat > 0 && failLat / passLat >= SLOWER_RATIO) return true;

  return false;
}

function annotate(node, patch) {
  // Shallow clone — fresh object so memoised React-Flow consumers re-render
  // without mutating the upstream graph.
  return { ...node, ...patch };
}

export function buildGraphDiff(failGraph, passGraph) {
  if (!failGraph || !passGraph) {
    return {
      failAnnotated: failGraph ?? null,
      passAnnotated: passGraph ?? null,
      summary: { added: 0, missing: 0, regressed: 0, shared: 0, failed: 0 },
    };
  }

  const failNodes = failGraph.nodes ?? [];
  const passNodes = passGraph.nodes ?? [];
  const failEdges = failGraph.edges ?? [];
  const passEdges = passGraph.edges ?? [];

  const passByKey = new Map();
  for (const n of passNodes) passByKey.set(keyOf(n), n);

  let added = 0;
  let missing = 0;
  let regressed = 0;
  let shared = 0;
  let failed = 0;

  // 1. Annotate the failing-side nodes. Failure point takes visual priority
  //    over diff status — a failed node is the headline, not a diff cue.
  const annotatedFailNodes = failNodes.map((node) => {
    const isSentinel = SENTINEL_TYPES.has(node?.type);
    const isFailurePoint = !isSentinel && errorCountOf(node) > 0;
    if (isFailurePoint) failed += 1;

    if (isSentinel) {
      return annotate(node, { _diffStatus: "matched" });
    }
    const match = passByKey.get(keyOf(node));
    let status;
    if (!match) {
      added += 1;
      status = "fail-only";
    } else if (isRegressed(node, match)) {
      regressed += 1;
      status = "matched-regressed";
    } else {
      shared += 1;
      status = "matched";
    }
    return annotate(node, {
      _diffStatus: status,
      _isFailurePoint: isFailurePoint || undefined,
    });
  });

  // 2. Pass-only (missing) nodes become ghosts injected into the failing graph.
  const failByKey = new Map();
  for (const n of annotatedFailNodes) failByKey.set(keyOf(n), n);

  const ghostNodes = [];
  const ghostIds = new Set(); // working-trace ids whose ghost we created
  for (const passNode of passNodes) {
    if (SENTINEL_TYPES.has(passNode?.type)) continue;
    if (failByKey.has(keyOf(passNode))) continue;
    missing += 1;
    ghostNodes.push({
      ...passNode,
      id: `${GHOST_PREFIX}${passNode.id}`,
      _diffStatus: "pass-only-ghost",
    });
    ghostIds.add(passNode.id);
  }

  // 3. Synthetic "skipped path" edges. For each working-graph edge whose
  //    TARGET is a missing/ghost node, mirror it into the failing graph.
  //    Source resolves to: the failing-side equivalent if shared (entry into
  //    the ghost branch), or another ghost (interior of a multi-hop missing
  //    chain). Edges whose target is shared aren't mirrored — they'd rejoin
  //    the real flow and create confusing duplicate connections.
  const skippedEdges = [];
  const passNodeById = new Map();
  for (const n of passNodes) passNodeById.set(n.id, n);

  for (const edge of passEdges) {
    if (!ghostIds.has(edge.target)) continue;

    let sourceId;
    if (ghostIds.has(edge.source)) {
      sourceId = `${GHOST_PREFIX}${edge.source}`;
    } else {
      const sourcePassNode = passNodeById.get(edge.source);
      if (!sourcePassNode) continue;
      const failParent = failByKey.get(keyOf(sourcePassNode));
      if (!failParent) continue;
      sourceId = failParent.id;
    }

    skippedEdges.push({
      source: sourceId,
      target: `${GHOST_PREFIX}${edge.target}`,
      transitionCount: 1,
      _skipped: true,
    });
  }

  const failAnnotated = {
    ...failGraph,
    nodes: [...annotatedFailNodes, ...ghostNodes],
    edges: [...failEdges, ...skippedEdges],
  };

  // 4. Pass-side annotations — reference view, lighter touch.
  const passAnnotated = {
    ...passGraph,
    nodes: passNodes.map((node) => {
      if (SENTINEL_TYPES.has(node?.type)) {
        return annotate(node, { _diffStatus: "matched" });
      }
      const match = failByKey.get(keyOf(node));
      if (!match) return annotate(node, { _diffStatus: "pass-only" });
      return annotate(node, { _diffStatus: "matched" });
    }),
  };

  return {
    failAnnotated,
    passAnnotated,
    summary: { added, missing, regressed, shared, failed },
  };
}
