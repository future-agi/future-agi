// W1-9 B5 (CP-08) · Paths compare per step (pure): one row per step node
// of either run, baseline vs candidate, from the two runs' aggregates
// (pathModel.aggregatePaths: visits, tries through, failing tries through,
// mean time, tokens, cost, failing checks). Every number is read from the
// aggregates; nothing is re-measured.
//   lookup_order · 52 → 48 visits · pass 61% → 83% · 1.2 s → 0.9 s ·
//   ✕ order_found 9 → 2
import { fmtCost, fmtMs, fmtPct } from "src/sections/common/format/fmt";

const SENTINEL = "__start__";

/** `✕ order_found 4, refund_amount 1` from a node / step's failing_checks. */
export const failingText = (s) => {
  const e = Object.entries(s?.failing_checks ?? {}).sort(
    (a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1),
  );
  return e.length ? `✕ ${e.map(([k, n]) => `${k} ${n}`).join(", ")}` : null;
};
const END = "__end__";

/** A node's metrics: visits, tries through, pass share, means, checks. */
export function nodeMetrics(node) {
  if (!node) return null;
  const visits = node.span_count ?? 0;
  const tries = node.trace_count ?? 0;
  return {
    visits,
    tries,
    passShare: tries ? (tries - (node.error_count ?? 0)) / tries : null,
    meanMs: visits ? node.avg_latency_ms ?? 0 : null,
    meanTokens: visits ? (node.total_tokens ?? 0) / visits : null,
    meanCost: visits ? (node.total_cost ?? 0) / visits : null,
    failing: { ...(node.failing_checks ?? {}) },
  };
}

const d = (a, b) => (a == null || b == null ? null : b - a);
const pts = (a, b) =>
  a == null || b == null ? null : Math.round((b - a) * 1000) / 10;

/**
 * Rows `{id, name, type, base, cand, delta, checks, only}` sorted by the
 * biggest change: steps only one run takes first, then the pass-share move
 * (pts), then the failing-check move, then visits.
 */
export function compareSteps(baseAgg, candAgg) {
  const nodes = (agg) =>
    new Map(
      (agg?.graph?.nodes ?? [])
        .filter((n) => n.id !== SENTINEL)
        .map((n) => [n.id, n]),
    );
  const A = nodes(baseAgg);
  const B = nodes(candAgg);
  const ids = [...new Set([...A.keys(), ...B.keys()])];
  return ids
    .map((id) => {
      const na = A.get(id) ?? null;
      const nb = B.get(id) ?? null;
      const base = nodeMetrics(na);
      const cand = nodeMetrics(nb);
      const names = [
        ...new Set([
          ...Object.keys(base?.failing ?? {}),
          ...Object.keys(cand?.failing ?? {}),
        ]),
      ];
      const checks = names
        .map((name) => {
          const a = base?.failing?.[name] ?? 0;
          const b = cand?.failing?.[name] ?? 0;
          return { name, base: a, cand: b, delta: b - a };
        })
        .sort(
          (x, y) =>
            Math.abs(y.delta) - Math.abs(x.delta) || (x.name < y.name ? -1 : 1),
        );
      const end = id === END;
      return {
        id,
        name: (na ?? nb).name,
        type: (na ?? nb).type,
        end,
        base,
        cand,
        only: na && !nb ? "base" : nb && !na ? "cand" : null,
        checks,
        delta: {
          visits: end ? null : d(base?.visits, cand?.visits),
          passPts: end ? null : pts(base?.passShare, cand?.passShare),
          ms: end ? null : d(base?.meanMs, cand?.meanMs),
          tokens: end ? null : d(base?.meanTokens, cand?.meanTokens),
          cost: end ? null : d(base?.meanCost, cand?.meanCost),
          checks: checks.reduce((t, c) => t + c.delta, 0),
        },
      };
    })
    .filter((r) => !r.end || r.checks.length)
    .sort(
      (x, y) =>
        Number(Boolean(y.only)) - Number(Boolean(x.only)) ||
        Math.abs(y.delta.passPts ?? 0) - Math.abs(x.delta.passPts ?? 0) ||
        Math.abs(y.delta.checks) - Math.abs(x.delta.checks) ||
        Math.abs(y.delta.visits ?? 0) - Math.abs(x.delta.visits ?? 0) ||
        (x.name < y.name ? -1 : 1),
    );
}

const arrow = (a, b, f) =>
  a == null && b == null
    ? null
    : `${a == null ? "—" : f(a)} → ${b == null ? "—" : f(b)}`;
const int = (v) => Math.round(v).toLocaleString("en-US");

/** The row's sentence (the table's title attribute and the walk's read). */
export function stepCompareText(r) {
  const parts = [
    r.end ? "End" : r.name,
    r.end ? null : `${arrow(r.base?.visits, r.cand?.visits, int)} visits`,
    r.end
      ? null
      : `pass ${arrow(r.base?.passShare, r.cand?.passShare, (v) => fmtPct(v, { dp: 0 }))}`,
    r.end ? null : arrow(r.base?.meanMs, r.cand?.meanMs, fmtMs),
    r.end ? null : `${arrow(r.base?.meanTokens, r.cand?.meanTokens, int)} tok`,
    r.end ? null : arrow(r.base?.meanCost, r.cand?.meanCost, fmtCost),
    ...r.checks.map((c) => `✕ ${c.name} ${c.base} → ${c.cand}`),
  ];
  return parts.filter(Boolean).join(" · ");
}

/**
 * Rows to open for a step: the example rows of the paths through it in
 * either run, failing first (pathModel keeps ≤ 6 per path, failing first).
 */
export function triesThrough(aggs, nodeId) {
  const out = [];
  (aggs ?? []).forEach((agg) =>
    (agg?.paths ?? [])
      .filter((p) => nodeId === END || p.steps.some((s) => s.name === nodeId))
      .sort((a, b) => b.failed - a.failed)
      .forEach((p) =>
        (p.example_rows ?? []).forEach(
          (id) => !out.includes(id) && out.push(id),
        ),
      ),
  );
  return out;
}
