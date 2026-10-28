// Geometry of the trend chart: a 100% stacked area of category share over
// run time, drawn as plain SVG. Rows and series come from buildAreaSeries.

const round = (value) => Math.round(value * 10) / 10;

/**
 * {inner, xs, bands, yOf} for rows drawn in a width x height box:
 *   inner  the plot area {left, right, top, bottom}
 *   xs     the x of each row (by time; evenly spaced when all times match)
 *   bands  [{id, color, d}] one closed path per series, bottom of the stack first
 */
export function trendGeometry({
  rows = [],
  series = [],
  width,
  height,
  margin = { top: 6, right: 12, bottom: 22, left: 40 },
}) {
  const inner = {
    left: margin.left,
    right: Math.max(margin.left + 1, width - margin.right),
    top: margin.top,
    bottom: Math.max(margin.top + 1, height - margin.bottom),
  };
  const times = rows.map((row) => row.time);
  const first = Math.min(...times);
  const last = Math.max(...times);
  const span = inner.right - inner.left;
  const xs = rows.map((row, i) =>
    round(
      last > first
        ? inner.left + ((row.time - first) / (last - first)) * span
        : inner.left + (rows.length > 1 ? (i / (rows.length - 1)) * span : 0),
    ),
  );
  const yOf = (share) =>
    round(inner.bottom - share * (inner.bottom - inner.top));
  const base = rows.map(() => 0);
  const bands = series.map((s) => {
    const lower = [...base];
    const upper = rows.map((row, i) => {
      base[i] += row.shares?.[s.id] || 0;
      return base[i];
    });
    const top = xs.map((x, i) => `${x},${yOf(upper[i])}`);
    const bottom = xs.map((x, i) => `${x},${yOf(lower[i])}`).reverse();
    return {
      id: s.id,
      color: s.color,
      d: `M${top.join("L")}L${bottom.join("L")}Z`,
    };
  });
  return { inner, xs, bands, yOf };
}

/** Index of the x closest to `x`, or -1 when there are none. */
export function nearestIndex(xs, x) {
  let best = -1;
  let distance = Infinity;
  xs.forEach((value, i) => {
    const d = Math.abs(value - x);
    if (d < distance) {
      distance = d;
      best = i;
    }
  });
  return best;
}

/**
 * The x-axis labels that fit: [{index, anchor}] in row order. The run on
 * screen, the latest and the first are placed first, then the rest in turn,
 * each only where its text (about `charPx` a character) clears every label
 * already placed by `gap` and isn't the same text again; a label near an edge
 * is anchored to stay inside [left, right]. However close runs are, labels
 * never overlap: the tooltip names every run.
 */
export function tickLabels({
  xs,
  texts,
  current = -1,
  left,
  right,
  charPx = 6.5,
  gap = 12,
}) {
  const order = [current, xs.length - 1, 0, ...xs.keys()].filter(
    (index, at, all) =>
      index >= 0 && index < xs.length && all.indexOf(index) === at,
  );
  const placed = [];
  order.forEach((index) => {
    const x = xs[index];
    const text = texts[index];
    const w = String(text).length * charPx;
    const [anchor, from, to] =
      x - w / 2 < left
        ? ["start", x, x + w]
        : x + w / 2 > right
          ? ["end", x - w, x]
          : ["middle", x - w / 2, x + w / 2];
    const clash = placed.some(
      (p) => p.text === text || (from < p.to + gap && to > p.from - gap),
    );
    if (!clash) placed.push({ index, anchor, text, from, to });
  });
  return placed
    .sort((a, b) => a.index - b.index)
    .map(({ index, anchor }) => ({ index, anchor }));
}
