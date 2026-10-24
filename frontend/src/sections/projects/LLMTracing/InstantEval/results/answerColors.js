import { alpha } from "@mui/material/styles";
import { ANSWER_TYPES } from "../instantEvalPayloads";
import { ERROR_ANSWER, SKIPPED_ANSWER } from "../instantEvalAnswers";

// Category hues, by the label's position in the question. None is purple,
// orange or red: those mean Yes, No and failed. Blue, pink, lime, jade, cyan
// and olive tell apart as any pair, for colour-blind readers too (the data-viz
// palette check, all pairs: CVD ΔE ≥ 8.8, normal ΔE ≥ 15.4); plum and green
// are told apart from their neighbours only. The dark steps are the same hues
// for the dark surface, and pass the same check against it.
export const CATEGORY_COLORS = [
  "#2A78D6",
  "#E87BA4",
  "#84CC16",
  "#038660",
  "#06B6D4",
  "#9A8C00",
  "#8B036F",
  "#05A92E",
];
export const CATEGORY_COLORS_DARK = [
  "#0160C1",
  "#E0609A",
  "#73A40A",
  "#0F825E",
  "#16A2C1",
  "#877403",
  "#BC0B97",
  "#02721C",
];
// The catch-all answer ("Something else", "Other") is the rest, so it is a
// quiet neutral rather than a hue; it still tells apart from the first five
// hues as any pair.
export const CATCH_ALL_COLOR = "#C3C2B7";
export const CATCH_ALL_COLOR_DARK = "#B5B3AC";
const CATCH_ALL =
  /^(something else|anything else|other|others|other reason|none|none of (these|the above)|n\/a|unknown|unclear|not sure|misc(ellaneous)?)$/i;
export const isCatchAll = (label) => CATCH_ALL.test(String(label ?? "").trim());
// A ninth and tenth label are not given a hue of their own: a generated hue
// would pass for one of the eight. They share a grey and are told apart by
// name (the legend, the tooltip, one panel each in the small multiples).
export const OTHER_CATEGORY_COLOR = "#898781";

// Yes and No get two strong hues that judge neither: the brand purple and an
// amber. Not red (that means failed) and not green ("No" is not always bad:
// "Did the customer ask for a human?").
export const YES_COLOR = "#7857FC";
export const NO_COLOR = "#F97316";

/**
 * The hue of the label at `index` (0-based), for a light or dark surface; a
 * catch-all label (`label`) is neutral.
 */
export function categoryColor(index, mode = "light", label) {
  if (isCatchAll(label))
    return mode === "dark" ? CATCH_ALL_COLOR_DARK : CATCH_ALL_COLOR;
  const colors = mode === "dark" ? CATEGORY_COLORS_DARK : CATEGORY_COLORS;
  return index >= 0 && index < colors.length
    ? colors[index]
    : OTHER_CATEGORY_COLOR;
}

// One answer's color: Yes in purple, No in amber, categories by position,
// failures and skips muted.
export function answerColor(item, answer, theme) {
  if (answer === ERROR_ANSWER) return alpha(theme.palette.error.main, 0.45);
  if (answer === SKIPPED_ANSWER) return theme.palette.grey[400];
  if (item.answerType === ANSWER_TYPES.CATEGORY)
    return categoryColor(
      item.labels.indexOf(answer),
      theme.palette.mode,
      answer,
    );
  return answer === "Yes" ? YES_COLOR : NO_COLOR;
}

// The distribution's entries in display order: labels, then failures, then
// skips.
export function distributionEntries(summary) {
  return [
    ...summary.labels.map((entry) => ({ ...entry, answer: entry.label })),
    ...(summary.errors.count
      ? [{ ...summary.errors, answer: ERROR_ANSWER, muted: true }]
      : []),
    ...(summary.skipped.count
      ? [{ ...summary.skipped, answer: SKIPPED_ANSWER, muted: true }]
      : []),
  ];
}
