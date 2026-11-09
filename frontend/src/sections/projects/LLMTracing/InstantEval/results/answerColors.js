import { alpha } from "@mui/material/styles";
import { dsPalette } from "src/theme/ds-tokens";
import { ANSWER_TYPES } from "../instantEvalPayloads";
import { ERROR_ANSWER, SKIPPED_ANSWER } from "../instantEvalAnswers";

// Stable slots follow the authored label order, not the current count/rank.
// The canonical data palette owns both modes; labels and legends still carry
// identity. Never cycle overflow labels onto an earlier categorical slot.
const lightPalette = dsPalette("light");
const darkPalette = dsPalette("dark");
const CATEGORY_SLOTS = [1, 4, 6];
export const CATEGORY_COLORS = CATEGORY_SLOTS.map(
  (slot) => lightPalette.data[slot],
);
// These canonical darker data steps validate on both the light and dark chart
// surfaces; the brighter dark UI steps do not separate enough for dense plots.
export const CATEGORY_COLORS_DARK = CATEGORY_COLORS;
// The catch-all answer stays neutral and explicitly named.
export const CATCH_ALL_COLOR = lightPalette.text.disabled;
export const CATCH_ALL_COLOR_DARK = darkPalette.text.disabled;
const CATCH_ALL =
  /^(something else|anything else|other|others|other reason|none|none of (these|the above)|n\/a|unknown|unclear|not sure|misc(ellaneous)?)$/i;
export const isCatchAll = (label) => CATCH_ALL.test(String(label ?? "").trim());
// Keep the three all-pairs-distinct chromatic slots. Labels beyond those slots
// share neutral marks, never another category's hue; names, tooltips and small
// multiples still expose each label separately with unchanged counts/actions.
export const OTHER_CATEGORY_COLOR = lightPalette.text.disabled;

// Boolean answers use two fixed nominal data slots, not success/failure
// colours: "No" is not necessarily bad ("Did the user ask for a human?").
export const YES_COLOR = lightPalette.data[0];
export const NO_COLOR = lightPalette.data[5];

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
    : mode === "dark"
      ? darkPalette.text.disabled
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
