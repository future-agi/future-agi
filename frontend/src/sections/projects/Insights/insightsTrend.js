// Geometry of the trend chart: a 100% stacked area of category share over
// run time, drawn as plain SVG. Rows and series come from buildAreaSeries.

const round = (value) => Math.round(value * 10) / 10;

// Width of a run drawn alone (a rediscovered run whose categories no other
// run shares): a column, as there is no span to fill.
const COLUMN_PX = 16;

/**
 * {inner, xs, bands, breaks, yOf} for rows drawn in a width x height box:
 *   inner   the plot area {left, right, top, bottom}
 *   xs      the x of each row (by time; evenly spaced when all times match)
 *   bands   [{id, color, d}] one path per series, bottom of the stack first
 *   breaks  [{index, x}] where a row found its categories afresh (`fresh`):
 *           nothing is drawn across, so no change between the runs either
 *           side is implied; a run alone between breaks is a column
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

  // Runs between breaks: [first, last] row indexes.
  const segments = [];
  rows.forEach((row, i) => {
    if (i === 0 || row.fresh) segments.push([i, i]);
    else segments[segments.length - 1][1] = i;
  });
  // A segment's left and right edges: its runs' x, or a column's sides,
  // never reaching a neighbour's.
  const edges = segments.map(([a, b]) => {
    if (a !== b) return [xs[a], xs[b]];
    const gaps = [xs[a] - xs[a - 1], xs[a + 1] - xs[a]].filter(Number.isFinite);
    const half = Math.max(
      1.5,
      Math.min(COLUMN_PX / 2, ...gaps.map((g) => g / 2)),
    );
    const x0 = Math.max(
      inner.left,
      Math.min(xs[a] - half, inner.right - 2 * half),
    );
    return [round(x0), round(x0 + 2 * half)];
  });

  const base = rows.map(() => 0);
  const bands = series.map((s) => {
    const lower = [...base];
    const upper = rows.map((row, i) => {
      base[i] += row.shares?.[s.id] || 0;
      return base[i];
    });
    const parts = segments.map(([a, b], k) => {
      const at = [];
      for (let i = a; i <= b; i += 1) at.push(i);
      // A series none of the segment's runs has draws nothing there.
      if (at.every((i) => upper[i] === lower[i])) return "";
      const pointsX = a === b ? edges[k] : at.map((i) => xs[i]);
      const rowsAt = a === b ? [a, a] : at;
      const top = pointsX.map((x, j) => `${x},${yOf(upper[rowsAt[j]])}`);
      const bottom = pointsX
        .map((x, j) => `${x},${yOf(lower[rowsAt[j]])}`)
        .reverse();
      return `M${top.join("L")}L${bottom.join("L")}Z`;
    });
    return { id: s.id, color: s.color, d: parts.join("") };
  });
  const breaks = segments.slice(1).map(([a], k) => ({
    index: a,
    x: round((edges[k][1] + edges[k + 1][0]) / 2),
  }));
  return { inner, xs, bands, breaks, yOf };
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
