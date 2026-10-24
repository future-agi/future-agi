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
 * Indexes of the rows that get an x-axis label: every row when there are
 * few, else an even spread that always keeps the first and last.
 */
export function tickIndexes(count, max = 8) {
  if (count <= max) return Array.from({ length: count }, (_, i) => i);
  const step = (count - 1) / (max - 1);
  return [
    ...new Set(Array.from({ length: max }, (_, i) => Math.round(i * step))),
  ];
}
