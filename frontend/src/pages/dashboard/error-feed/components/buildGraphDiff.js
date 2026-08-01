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
 *   `node.id` — buildTraceGraph already assigns a unique id per node (either
 *   the explicit `graph.node.id`, the inferred `type:name` group key, or a
 *   `__start__` / `__end__` sentinel). Two distinct nodes that happen to share
 *   a `(type, name)` pair therefore keep separate entries instead of one
 *   silently overwriting the other in the lookup map.
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
// Sentinel `type` values are lowercased before the check, so this covers
// "start"/"end" as well as capitalized "Start"/"End".
const SENTINEL_TYPES = new Set(["start", "end"]);
const GHOST_PREFIX = "ghost-";

// Unique per-node key. buildTraceGraph guarantees a stable, unique `id`; we
// fall back to `(type, name)` only for the (unexpected) case of a node with no
// id so the map never collapses two real nodes onto one entry.
function keyOf(node) {
  if (node?.id != null) return String(node.id);
  const type = String(node?.type ?? "")
    .toLowerCase()
    .trim();
  const name = String(node?.name ?? "")
    .toLowerCase()
    .trim();
  return `${type}|${name}`;
}

// True when a node's `type` is a Start/End sentinel, case-insensitively.
function isSentinel(node) {
  return SENTINEL_TYPES.has(
    String(node?.type ?? "")
      .toLowerCase()
      .trim(),
  );
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

  // 1. Annotate failing-side nodes. Failure point takes priority over diff status.
  const annotatedFailNodes = failNodes.map((node) => {
    const sentinel = isSentinel(node);
    const isFailurePoint = !sentinel && errorCountOf(node) > 0;
    if (isFailurePoint) failed += 1;

    if (sentinel) {
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
      // Always a boolean — non-failure nodes carry `false`, not `undefined`.
      _isFailurePoint: isFailurePoint,
    });
  });

  // 2. Pass-only (missing) nodes become ghosts injected into the failing graph.
  const failByKey = new Map();
  for (const n of annotatedFailNodes) failByKey.set(keyOf(n), n);

  const ghostNodes = [];
  const ghostIds = new Set(); // working-trace ids whose ghost we created
  for (const passNode of passNodes) {
    if (isSentinel(passNode)) continue;
    if (failByKey.has(keyOf(passNode))) continue;
    missing += 1;
    ghostNodes.push({
      ...passNode,
      // React Flow needs a unique id within the merged graph, so the ghost
      // gets a prefixed id — but keep the working-trace id on `_originalId`
      // so callers can still trace it back to the pass-side node.
      id: `${GHOST_PREFIX}${passNode.id}`,
      _originalId: passNode.id,
      _diffStatus: "pass-only-ghost",
    });
    ghostIds.add(passNode.id);
  }

  // 3. Mirror working-graph edges whose target is a ghost into the failing graph.
  //    Edges whose target is shared aren't mirrored — they'd create duplicate connections.
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
      // Preserve the source edge's id (prefixed to stay unique against the
      // real fail-side edges) when one is present.
      ...(edge.id != null ? { id: `${GHOST_PREFIX}${edge.id}` } : {}),
      source: sourceId,
      target: `${GHOST_PREFIX}${edge.target}`,
      transitionCount: edge.transitionCount ?? 1,
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
      if (isSentinel(node)) {
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
