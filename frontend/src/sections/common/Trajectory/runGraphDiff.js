// Two-run graph diff (R4 lane T, R4-SPEC D13): an adapter over the Error
// Feed's buildGraphDiff (imported, never copied). A = the baseline run, B =
// the other run. buildGraphDiff(B, A) gives B's graph with A-only nodes
// ghosted in; this module remaps its statuses to the two-run overlay that
// AgentGraph draws (`only-a`, `only-b`, `both`, `both-regressed`) and adds:
//   _diffColor   the run colour of an only-A / only-B node or edge
//   _visits      {a, b}: span count of the node in each run
//   _loops       ≥ 2 when the node repeated back to back in a run
//   _isFailurePoint  the node holding `First wrong step` of either run
//                (from markers; never from error counts)
// Edges are rebuilt: B's edges are `both` / `only-b`, A's edges that B lacks
// are `only-a` (dashed) between the merged nodes.
//
// Node identity: buildTraceGraph ids, except that a run's final reply node
// (`respond`, `reply`, `final_answer`, …) matches the other run's final
// reply (`reply` key), so `respond` in Support Agent v3 and `reply` in the
// LangGraph agent read as one step. Pure; returns canonical AgentGraph data.
import { buildGraphDiff } from "src/pages/dashboard/error-feed/components/buildGraphDiff";

const GHOST = "ghost-";
const SENTINEL = new Set(["start", "end"]);
const REPLY_NAMES = new Set([
  "respond",
  "reply",
  "final_answer",
  "final",
  "answer",
  "assistant",
]);
export const REPLY_KEY = "reply";

const isSentinel = (n) => SENTINEL.has(String(n?.type ?? "").toLowerCase());
const isReplyNode = (n) =>
  !isSentinel(n) && REPLY_NAMES.has(String(n?.name ?? "").toLowerCase());

/** Remap reply nodes to the shared `reply` key (once per graph). */
function keyed(graph) {
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const taken = new Set(nodes.map((n) => n.id));
  const map = new Map();
  const reply = nodes.filter(isReplyNode);
  if (
    reply.length === 1 &&
    (!taken.has(REPLY_KEY) || reply[0].id === REPLY_KEY)
  )
    map.set(reply[0].id, REPLY_KEY);
  const idOf = (id) => map.get(id) ?? id;
  return {
    graph: {
      ...graph,
      nodes: nodes.map((n) =>
        map.has(n.id) ? { ...n, id: idOf(n.id), _sourceId: n.id } : n,
      ),
      edges: edges.map((e) => ({
        ...e,
        source: idOf(e.source),
        target: idOf(e.target),
      })),
    },
    idOf,
  };
}

const STATUS = {
  "fail-only": "only-b",
  "pass-only-ghost": "only-a",
  matched: "both",
  "matched-regressed": "both-regressed",
};

const edgeKey = (s, t) => `${s}->${t}`;

/**
 * @param {{nodes, edges}} graphA  baseline run (buildTraceGraph output)
 * @param {{nodes, edges}} graphB  other run
 * @param {{markers?: {a?: string|null, b?: string|null},
 *   colors?: {a?: string, b?: string}}} [opts]  `markers` = node ids (or
 *   names) of each run's First wrong step
 * @returns {{nodes, edges, path_edges, nodeToSpanIds: {a, b},
 *   summary: {onlyA, onlyB, both, regressed, failed, loops}}}
 */
export function runGraphDiff(
  graphA,
  graphB,
  { markers = {}, colors = {} } = {},
) {
  const A = keyed(graphA);
  const B = keyed(graphB);
  const ga = A.graph;
  const gb = B.graph;
  const { failAnnotated } = buildGraphDiff(gb, ga);

  const aById = new Map(ga.nodes.map((n) => [n.id, n]));
  const bById = new Map(gb.nodes.map((n) => [n.id, n]));
  const loopsOf = (graph) => {
    const out = new Map();
    graph.edges.forEach((e) => {
      if (e.is_self_loop || e.source === e.target)
        out.set(e.source, (out.get(e.source) ?? 0) + e.transition_count);
    });
    return out;
  };
  const loopsA = loopsOf(ga);
  const loopsB = loopsOf(gb);
  const markerIds = (m) => {
    if (!m) return [];
    return [m, A.idOf(m), B.idOf(m)];
  };
  const failKeys = new Set([...markerIds(markers.a), ...markerIds(markers.b)]);
  const isFailNode = (n, key) =>
    failKeys.has(key) ||
    failKeys.has(n.name) ||
    Boolean(n._sourceId && failKeys.has(n._sourceId));

  const summary = {
    onlyA: [],
    onlyB: [],
    both: [],
    regressed: [],
    failed: [],
    loops: [],
  };
  const idMap = new Map(); // merged key → merged node id
  const nodes = failAnnotated.nodes.map((n) => {
    const key = n._originalId ?? n.id;
    idMap.set(key, n.id);
    if (isSentinel(n)) {
      const { _diffStatus, _isFailurePoint, ...rest } = n;
      return rest;
    }
    const status = STATUS[n._diffStatus] ?? "both";
    const a = aById.get(key);
    const b = bById.get(key);
    const loops = Math.max(
      loopsA.has(key) ? a?.span_count ?? 0 : 0,
      loopsB.has(key) ? b?.span_count ?? 0 : 0,
    );
    const failed = isFailNode(n, key);
    const name = status === "only-a" ? a?.name ?? n.name : n.name;
    const out = {
      ...n,
      name,
      _diffStatus: status,
      _visits: { a: a?.span_count ?? 0, b: b?.span_count ?? 0 },
      _isFailurePoint: failed,
      ...(status === "only-a" && colors.a ? { _diffColor: colors.a } : {}),
      ...(status === "only-b" && colors.b ? { _diffColor: colors.b } : {}),
      ...(loops > 1 ? { _loops: loops } : {}),
    };
    const bucket =
      status === "only-a"
        ? summary.onlyA
        : status === "only-b"
          ? summary.onlyB
          : summary.both;
    bucket.push(name);
    if (status === "both-regressed") summary.regressed.push(name);
    if (failed) summary.failed.push(name);
    if (loops > 1)
      summary.loops.push({
        name,
        a: loopsA.has(key) ? a?.span_count ?? 0 : 0,
        b: loopsB.has(key) ? b?.span_count ?? 0 : 0,
      });
    return out;
  });

  // Edges: B's real edges, then A's edges that B doesn't have.
  const bKeys = new Set(gb.edges.map((e) => edgeKey(e.source, e.target)));
  const aKeys = new Set(ga.edges.map((e) => edgeKey(e.source, e.target)));
  const strip = ({ _skipped, ...e }) => e;
  const edges = [
    ...gb.edges.map((e) => {
      const both = aKeys.has(edgeKey(e.source, e.target));
      return {
        ...strip(e),
        _diffStatus: both ? "both" : "only-b",
        ...(!both && colors.b ? { _diffColor: colors.b } : {}),
      };
    }),
    ...ga.edges
      .filter((e) => !bKeys.has(edgeKey(e.source, e.target)))
      .map((e) => ({
        ...strip(e),
        source: idMap.get(e.source) ?? `${GHOST}${e.source}`,
        target: idMap.get(e.target) ?? `${GHOST}${e.target}`,
        _diffStatus: "only-a",
        ...(colors.a ? { _diffColor: colors.a } : {}),
      }))
      .filter(
        (e) =>
          nodes.some((n) => n.id === e.source) &&
          nodes.some((n) => n.id === e.target),
      ),
  ];

  return {
    nodes,
    edges,
    path_edges: [],
    nodeToSpanIds: {
      a: remapSpans(graphA?.nodeToSpanIds, A.idOf, idMap),
      b: remapSpans(graphB?.nodeToSpanIds, B.idOf, idMap),
    },
    summary,
  };
}

/** `{mergedNodeId: spanIds}` for one run. */
function remapSpans(nodeToSpanIds, idOf, idMap) {
  const out = {};
  Object.entries(nodeToSpanIds ?? {}).forEach(([id, spans]) => {
    const merged = idMap.get(idOf(id));
    if (merged) out[merged] = spans;
  });
  return out;
}

/**
 * The node whose graph differs most from the baseline among candidates, by
 * node-set difference (Task compare `Compare with ▾` default at 3–4 lanes).
 * @param {{nodes}} base
 * @param {{id: string, graph: {nodes}}[]} others
 */
export function mostDifferent(base, others) {
  const names = (g) =>
    new Set(
      (keyed(g).graph.nodes ?? [])
        .filter((n) => !isSentinel(n))
        .map((n) => n.id),
    );
  const b = names(base);
  let best = null;
  (others ?? []).forEach((o) => {
    const s = names(o.graph);
    let d = 0;
    s.forEach((x) => !b.has(x) && (d += 1));
    b.forEach((x) => !s.has(x) && (d += 1));
    if (!best || d > best.d) best = { id: o.id, d };
  });
  return best?.id ?? null;
}
