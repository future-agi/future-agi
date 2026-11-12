// Series colours for gateway charts that split by a dimension (model,
// provider, application, service). Picked from the Future AGI theme's own
// scales (theme/palette.js), no new colour: eight distinct hues in a fixed
// slot order, then a neutral grey for "Other". A chart draws the top 8 groups
// and folds the rest into Other, as Vercel and OpenRouter do, so no colour
// repeats and the legend stays short.
//
// The slot order was checked with the dataviz palette validator (adjacent
// slots: lightness band, chroma, colour-blind separation dE >= 8, normal
// vision dE >= 15, 3:1 on the chart surface). Light: all five checks pass.
// Dark: the same hues on #111; slot 7 takes the theme's teal (info.success)
// because no deeper blue clears 3:1 there, and sits 0.03 above the
// validator's lightness band (its contrast is about 8:1).
// warning.main and success.main (under 3:1 on white) and secondary.main (a
// near twin of primary) are not used.

export const TOP_SERIES = 8;
export const OTHER_SERIES = "Other";

/** The eight series hues, in slot order, for the theme's mode. */
export function seriesPalette(theme) {
  const p = theme.palette;
  const dark = p.mode === "dark";
  return [
    p.purple[500],
    p.orange[500],
    p.blue[600],
    p.red[500],
    p.pink[700],
    p.yellow[800],
    dark ? p.info.success : p.blue[800],
    p.green[600],
  ];
}

/** "Other": a neutral grey, no hue. */
export function otherSeriesColor(theme) {
  return theme.palette.mode === "dark"
    ? theme.palette.black[400]
    : theme.palette.black[500];
}

/**
 * The groups a chart draws: the top `top` by `rank` (a point's value, summed
 * over the window), then "Other" when more remain. Returns the names in
 * slot order and whether Other is drawn.
 */
export function topGroups(groups, rank, top = TOP_SERIES) {
  const totals = Object.entries(groups || {}).map(([name, points]) => [
    name,
    (points || []).reduce((sum, point) => sum + (Number(rank(point)) || 0), 0),
  ]);
  totals.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  // The API's own "Other" (it folds small tag groups) stays Other.
  const named = totals.filter(([name]) => name !== OTHER_SERIES);
  const own = totals.length - named.length;
  return {
    names: named.slice(0, top).map(([name]) => name),
    rest: [
      ...named.slice(top).map(([name]) => name),
      ...(own ? [OTHER_SERIES] : []),
    ],
  };
}

/**
 * Chart series for the top groups and, when any remain, "Other" with their
 * values summed per bucket. `value` reads a point's y.
 */
export function groupedSeries(groups, { names, rest }, value) {
  const toPoint = (point) => ({
    x: new Date(point.bucket).getTime(),
    y: Number(value(point)) || 0,
  });
  const series = names.map((name) => ({
    name,
    data: (groups[name] || []).map(toPoint),
  }));
  if (rest.length) {
    const sums = new Map();
    rest.forEach((name) =>
      (groups[name] || []).forEach((point) => {
        const { x, y } = toPoint(point);
        sums.set(x, (sums.get(x) || 0) + y);
      }),
    );
    series.push({
      name: OTHER_SERIES,
      data: [...sums.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([x, y]) => ({ x, y })),
    });
  }
  return series;
}

/** The colours for groupedSeries' output: slots in order, Other grey. */
export function groupedColors(theme, { names, rest }) {
  const hues = seriesPalette(theme).slice(0, names.length);
  return rest.length ? [...hues, otherSeriesColor(theme)] : hues;
}

/**
 * One colour per named group, in the order given (a breakdown, highest
 * first): the hues by slot, any "Other" in grey.
 */
export function colorsForNames(theme, names) {
  const hues = seriesPalette(theme);
  let slot = 0;
  return names.map((name) =>
    name === OTHER_SERIES || name === "other"
      ? otherSeriesColor(theme)
      : hues[slot++ % hues.length],
  );
}
