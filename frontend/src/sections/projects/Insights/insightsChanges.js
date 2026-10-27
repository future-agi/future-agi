import { formatCount, formatPercent } from "./insightsFormat";

// "What changed since the last run": result.changes compares this run with
// the previous one of the same report.
//   {previous_run_id, previous_finished_at,
//    categories: [{category_id, name, share, previous_share, delta, count,
//                  previous_count, q, significant}],
//    new_category_ids: [...],
//    metrics: [{metric, label, value, previous, significant}]}

// Metrics where a rise is bad or good; anything else is neutral.
const HIGHER_IS_WORSE = new Set([
  "failure_rate",
  "unresolved_rate",
  "error_rate",
  "negative_rate",
  "escalated_rate",
  "latency_ms",
  "cost",
]);
const HIGHER_IS_BETTER = new Set(["sentiment", "resolved_rate"]);

/** 0.12 -> "+12 pts", -0.004 -> "−<1 pt", 0 -> "0 pts". */
export function formatPts(delta) {
  if (!Number.isFinite(delta)) return "–";
  const pts = delta * 100;
  const sign = pts > 0 ? "+" : pts < 0 ? "−" : "";
  const size = Math.abs(pts);
  if (size === 0) return "0 pts";
  if (size < 0.5) return `${sign}<1 pt`;
  const rounded = Math.round(size);
  return `${sign}${rounded} ${rounded === 1 ? "pt" : "pts"}`;
}

const isRateMetric = (metric) =>
  /_rate$/.test(metric || "") || metric === "share";

function formatMetricValue(metric, value) {
  if (!Number.isFinite(value)) return "–";
  if (isRateMetric(metric)) return formatPercent(value);
  if (metric === "sentiment") return value.toFixed(1);
  if (metric === "latency_ms")
    return value < 1000
      ? `${Math.round(value)} ms`
      : `${(value / 1000).toFixed(1)} s`;
  if (metric === "cost") return `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
  return Number.isInteger(value) ? formatCount(value) : value.toFixed(1);
}

const metricTone = (metric, value, previous) => {
  if (
    !Number.isFinite(value) ||
    !Number.isFinite(previous) ||
    value === previous
  )
    return "neutral";
  const higher = value > previous;
  if (HIGHER_IS_WORSE.has(metric)) return higher ? "worse" : "better";
  if (HIGHER_IS_BETTER.has(metric)) return higher ? "better" : "worse";
  return "neutral";
};

/**
 * Compact cards, strongest first: new categories, then significant moves by
 * size, then the rest. Each card:
 *   {key, kind: "category" | "new" | "metric", direction: "up" | "down" | "flat",
 *    name, from, to, delta, deltaText, count, significant, tone, categoryId}
 * tone is "worse" | "better" | "neutral"; a category's share has no good or
 * bad direction, so only metrics carry one.
 */
// New categories: listed in new_category_ids or flagged is_new on their row.
const newIds = (changes) =>
  new Set([
    ...(changes.new_category_ids || []),
    ...(changes.categories || [])
      .filter((c) => c?.is_new && c.category_id)
      .map((c) => c.category_id),
  ]);

// A sub that moved exactly as its own top did (a lone sub has its top's
// counts) is the same move: one card, the top's. `parentOf` names a sub's top;
// another top that happens to share the counts is a different move.
const echoesOfTops = (items, parentOf) => {
  const moves = new Map(
    items
      .filter((c) => c?.level === 1 && c.category_id)
      .map((c) => [c.category_id, `${c.count}:${c.previous_count}`]),
  );
  return (c) =>
    c?.level === 2 &&
    c.count != null &&
    moves.get(parentOf?.(c.category_id)) === `${c.count}:${c.previous_count}`;
};

export function buildChangeCards(changes, parentOf) {
  if (!changes || typeof changes !== "object") return [];
  const fresh = newIds(changes);
  const cards = [];
  const items = changes.categories || [];
  const echo = echoesOfTops(items, parentOf);
  const echoed = new Set();
  items.forEach((c) => {
    if (!c?.category_id) return;
    if (echo(c)) {
      echoed.add(c.category_id);
      return;
    }
    const share = Number(c.share);
    const previous = Number(c.previous_share);
    const delta = Number.isFinite(c.delta)
      ? c.delta
      : Number.isFinite(share) && Number.isFinite(previous)
        ? share - previous
        : NaN;
    if (fresh.has(c.category_id)) {
      cards.push({
        key: `new-${c.category_id}`,
        kind: "new",
        direction: "up",
        name: c.name,
        categoryId: c.category_id,
        count: c.count,
        to: formatPercent(share),
        delta,
        deltaText: formatPts(delta),
        significant: true,
        tone: "neutral",
      });
      return;
    }
    if (!Number.isFinite(delta)) return;
    cards.push({
      key: `cat-${c.category_id}`,
      kind: "category",
      direction: delta > 0 ? "up" : delta < 0 ? "down" : "flat",
      name: c.name,
      categoryId: c.category_id,
      count: c.count,
      from: formatPercent(previous),
      to: formatPercent(share),
      delta,
      deltaText: formatPts(delta),
      significant: Boolean(c.significant),
      tone: "neutral",
    });
  });
  // A new category id with no row in `categories` still gets a card; a new
  // sub that echoed its new top has the top's.
  fresh.forEach((id) => {
    if (echoed.has(id) || cards.some((card) => card.categoryId === id)) return;
    cards.push({
      key: `new-${id}`,
      kind: "new",
      direction: "up",
      name: "",
      categoryId: id,
      significant: true,
      tone: "neutral",
    });
  });
  (changes.metrics || []).forEach((m) => {
    if (!m?.metric) return;
    const value = Number(m.value);
    const previous = Number(m.previous);
    const delta = value - previous;
    cards.push({
      key: `metric-${m.metric}`,
      kind: "metric",
      direction: delta > 0 ? "up" : delta < 0 ? "down" : "flat",
      name: m.label || m.metric,
      from: formatMetricValue(m.metric, previous),
      to: formatMetricValue(m.metric, value),
      delta,
      deltaText: isRateMetric(m.metric) ? formatPts(delta) : "",
      significant: Boolean(m.significant),
      tone: metricTone(m.metric, value, previous),
    });
  });
  const rank = (card) => (card.kind === "new" ? 0 : card.significant ? 1 : 2);
  // Shares and rates compare by points; a sentiment or latency move has no
  // common scale with them, so it sorts after them within its rank.
  const size = (card) =>
    card.kind === "metric" && !card.deltaText ? -1 : Math.abs(card.delta || 0);
  return cards.sort((a, b) => rank(a) - rank(b) || size(b) - size(a));
}

/** Per category id: {delta, significant, isNew} for the categories table. */
export function changeByCategory(changes) {
  const map = new Map();
  if (!changes) return map;
  const fresh = newIds(changes);
  (changes.categories || []).forEach((c) => {
    if (!c?.category_id) return;
    const delta = Number.isFinite(c.delta)
      ? c.delta
      : Number(c.share) - Number(c.previous_share);
    map.set(c.category_id, {
      delta,
      significant: Boolean(c.significant),
      isNew: fresh.has(c.category_id),
      previousShare: c.previous_share,
    });
  });
  fresh.forEach((id) => {
    if (!map.has(id))
      map.set(id, { delta: NaN, significant: true, isNew: true });
  });
  return map;
}
