// Explore's chart board (W5-5 B4, AG-06). Pure. The board lives in the URL
// key `board` so a reload and a saved view keep it:
//   board = card | card | …   card = measure~chart~rows~w   (w: 1 half, 2 full)
// e.g. `eval:tone~bars~meta:difficulty~1|cost~bars~none~2`. At most
// BOARD_MAX cards (the scope's limit); unknown charts are dropped.

export const BOARD_MAX = 12;
// Every chart kind of the pivot's chart picker (WorkbenchRail CHARTS).
// `group: false`: the chart ignores the Group (Trend plots each column's
// total; Delta bars and the scatter compare two runs). `measure: false`:
// Delta bars show every metric of the run, so the card stores `all`.
export const BOARD_CHARTS = Object.freeze([
  { key: "bars", label: "Bars by group" },
  {
    key: "distribution",
    label: "Distribution",
    group: false,
    note: "Distribution draws the spread of row values per run; it takes no group.",
  },
  { key: "heatmap", label: "Heatmap" },
  {
    key: "cost_quality",
    label: "Cost vs quality",
    group: false,
    note: "Cost vs quality draws one point per run; it takes no group.",
  },
  {
    key: "trend",
    label: "Trend",
    group: false,
    note: "Trend draws one point per column, runs oldest first; it takes no group.",
  },
  {
    key: "scatter2",
    label: "Two runs (scatter)",
    group: false,
    note: "Two runs (scatter) draws one point per row: the baseline against a candidate.",
  },
  {
    key: "delta_bars",
    label: "Delta bars",
    group: false,
    measure: false,
    note: "Delta bars show every metric of a candidate run vs the baseline, with 95% intervals.",
  },
]);
export const ALL_METRICS = "all";
export const chartOf = (key) => BOARD_CHARTS.find((c) => c.key === key) ?? null;
const CHART_KEYS = new Set(BOARD_CHARTS.map((c) => c.key));
const SEP = "|";
const FIELD = "~";

const clean = (s) => String(s ?? "").replace(/[|~]/g, "");

/** Cards from the URL value (ids are positions, stable for one render). */
export function parseBoard(value) {
  if (!value) return [];
  return String(value)
    .split(SEP)
    .map((part) => part.split(FIELD))
    .filter(([measure, chart]) => measure && CHART_KEYS.has(chart))
    .slice(0, BOARD_MAX)
    .map(([measure, chart, rows, w], i) => ({
      id: `c${i}`,
      measure,
      chart,
      rows: rows || "none",
      full: w === "2",
    }));
}

/** The URL value of a board ("" for an empty board, so the key is dropped). */
export function serializeBoard(cards) {
  return (cards ?? [])
    .slice(0, BOARD_MAX)
    .map((c) =>
      [
        clean(c.measure),
        clean(c.chart),
        clean(c.rows || "none"),
        c.full ? "2" : "1",
      ].join(FIELD),
    )
    .join(SEP);
}

export const canAdd = (cards) => (cards?.length ?? 0) < BOARD_MAX;

export function addCard(cards, card) {
  if (!canAdd(cards) || !CHART_KEYS.has(card?.chart) || !card?.measure)
    return cards;
  return [...cards, { ...card, rows: card.rows || "none", full: !!card.full }];
}

export function duplicateCard(cards, index) {
  if (!canAdd(cards) || !cards[index]) return cards;
  return [
    ...cards.slice(0, index + 1),
    { ...cards[index] },
    ...cards.slice(index + 1),
  ];
}

export const removeCard = (cards, index) => cards.filter((_, i) => i !== index);

export const toggleFull = (cards, index) =>
  cards.map((c, i) => (i === index ? { ...c, full: !c.full } : c));

/** Move the card at `from` to position `to` (drag and drop). */
export function moveCard(cards, from, to) {
  if (from === to || !cards[from] || to < 0 || to >= cards.length) return cards;
  const next = [...cards];
  const [card] = next.splice(from, 1);
  next.splice(to, 0, card);
  return next;
}

/**
 * `Tone by difficulty`, `Cost per attempt by run` (bars); other kinds lead
 * with their name (`Trend · Tone`, `Heatmap · Tone by difficulty`,
 * `Delta bars · every metric vs the baseline`), so two cards on one measure
 * never share a title.
 */
export function cardTitle(card, { measureLabel, groupLabel }) {
  const by =
    !card.rows || card.rows === "none" ? "run" : groupLabel.toLowerCase();
  const chart = chartOf(card.chart);
  if (!chart || card.chart === "bars") return `${measureLabel} by ${by}`;
  if (chart.measure === false)
    return `${chart.label} · every metric vs the baseline`;
  if (chart.group === false) return `${chart.label} · ${measureLabel}`;
  return `${chart.label} · ${measureLabel} by ${by}`;
}
