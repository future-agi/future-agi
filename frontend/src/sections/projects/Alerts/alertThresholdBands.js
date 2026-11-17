import { alertThresholdBand } from "./common";

// A level saved as infinity or not-a-number has words and no number: the
// server sends its value as null with the words beside it, and the edit form
// keeps its field empty.
const hasNoNumber = (level) =>
  Boolean(level?.words) && !Number.isFinite(level?.value);

// The bands a static alert's chart shades, in the alert's direction: from the
// top of the chart to the first level, between the two levels, and from the
// last level to the bottom. A level with no number draws no line, so a band
// that would start or end at it is left out instead of drawn at 0.
export function staticThresholdBands(
  theme,
  { thresholdOperator, critical, warning, max, min, dangerAlpha },
) {
  const top = { value: max };
  const bottom = { value: min };
  const bands =
    thresholdOperator === "less_than"
      ? [
          [top, warning, "success", 0.03],
          [warning, critical, "warning", 0.03],
          [critical, bottom, "danger", dangerAlpha],
        ]
      : [
          [top, critical, "danger", dangerAlpha],
          [critical, warning, "warning", 0.03],
          [warning, bottom, "success", 0.03],
        ];
  return bands
    .filter(([from, to]) => !hasNoNumber(from) && !hasNoNumber(to))
    .map(([from, to, role, fillAlpha]) => ({
      value: from.value,
      y2: to.value,
      ...alertThresholdBand(theme, role, fillAlpha),
    }));
}
