/**
 * buildGraphDiff — diff two AgentGraphs (from buildTraceGraph) and return
 * annotated copies of each side ready for `AgentGraph`.
 *
 * Each node is matched by its (type, name) pair — both are stable identifiers
 * derived from the span attributes. The annotation appears as `data._diffStatus`
 * on every node:
 *
 *   - "fail-only"        → node exists only in the failing graph (extra step)
 *   - "pass-only"        → node exists only in the working graph (missed step)
 *   - "matched-regressed" → node exists in both, but the failing copy has
 *                           errors and/or is significantly slower
 *   - "matched"          → exists in both with comparable metrics
 *
 * Sentinel start/end nodes are always treated as "matched" — they're not
 * meaningful diffs and would otherwise noise up the visual.
 *
 * The original `failGraph` / `passGraph` objects are NOT mutated.
 */

const SLOWER_RATIO = 1.5; // failing.avg ≥ 1.5× working.avg counts as regressed
const SENTINEL_TYPES = new Set(["start", "end"]);

function keyOf(node) {
  // Lowercase + trim guards against minor casing/whitespace drift.
  const type = String(node?.data?.type ?? "").toLowerCase().trim();
  const name = String(node?.data?.name ?? "").toLowerCase().trim();
  return `${type}|${name}`;
}

function isRegressed(failNode, passNode) {
  const failErr = failNode?.data?.error_count ?? 0;
  const passErr = passNode?.data?.error_count ?? 0;
  if (failErr > 0 && passErr === 0) return true;

  const failLat = failNode?.data?.avg_latency_ms ?? 0;
  const passLat = passNode?.data?.avg_latency_ms ?? 0;
  if (passLat > 0 && failLat / passLat >= SLOWER_RATIO) return true;

  return false;
}

function annotate(node, status) {
  // Shallow clone — React Flow tolerates new node identities each render and
  // the AgentGraph component is keyed by node.id, so a fresh object is fine.
  return {
    ...node,
    data: {
      ...node.data,
      _diffStatus: status,
    },
  };
}

export function buildGraphDiff(failGraph, passGraph) {
  if (!failGraph || !passGraph) {
    return {
      failAnnotated: failGraph ?? null,
      passAnnotated: passGraph ?? null,
      summary: { added: 0, missing: 0, regressed: 0, shared: 0 },
    };
  }

  const failNodes = failGraph.nodes ?? [];
  const passNodes = passGraph.nodes ?? [];

  const passByKey = new Map();
  for (const n of passNodes) passByKey.set(keyOf(n), n);

  const failByKey = new Map();
  for (const n of failNodes) failByKey.set(keyOf(n), n);

  let added = 0;
  let missing = 0;
  let regressed = 0;
  let shared = 0;

  const failAnnotated = {
    ...failGraph,
    nodes: failNodes.map((node) => {
      if (SENTINEL_TYPES.has(node?.data?.type)) {
        return annotate(node, "matched");
      }
      const match = passByKey.get(keyOf(node));
      if (!match) {
        added += 1;
        return annotate(node, "fail-only");
      }
      if (isRegressed(node, match)) {
        regressed += 1;
        return annotate(node, "matched-regressed");
      }
      shared += 1;
      return annotate(node, "matched");
    }),
  };

  const passAnnotated = {
    ...passGraph,
    nodes: passNodes.map((node) => {
      if (SENTINEL_TYPES.has(node?.data?.type)) {
        return annotate(node, "matched");
      }
      const match = failByKey.get(keyOf(node));
      if (!match) {
        missing += 1;
        return annotate(node, "pass-only");
      }
      // Don't double-count regressions or shared on the pass side; the fail
      // side already accounted for them.
      return annotate(node, "matched");
    }),
  };

  return {
    failAnnotated,
    passAnnotated,
    summary: { added, missing, regressed, shared },
  };
}
