import { alpha } from "@mui/material/styles";
import { dsPalette } from "src/theme/ds-tokens";
import { ANSWER_TYPES } from "../instantEvalPayloads";
import { ERROR_ANSWER, SKIPPED_ANSWER } from "../instantEvalAnswers";

// Stable slots follow the authored label order, not the current count/rank.
// Categories take blue, teal, pink and lime from the data set, each mode with
// its own step: never a status hue (red reads as "couldn't answer") and never
// the Yes or No hue, so a cross-tab of a Yes/No question against a category
// question gives each colour one meaning. Labels and legends still carry
// identity. Never cycle overflow labels onto an earlier categorical slot.
const lightPalette = dsPalette("light");
const darkPalette = dsPalette("dark");
export const CATEGORY_SLOTS = [1, 2, 4, 6];
export const CATEGORY_COLORS = CATEGORY_SLOTS.map((i) => lightPalette.data[i]);
export const CATEGORY_COLORS_DARK = CATEGORY_SLOTS.map(
  (i) => darkPalette.data[i],
);
// The catch-all answer stays neutral and explicitly named.
export const CATCH_ALL_COLOR = lightPalette.text.disabled;
export const CATCH_ALL_COLOR_DARK = darkPalette.text.disabled;
const CATCH_ALL =
  /^(something else|anything else|other|others|other reason|none|none of (these|the above)|n\/a|unknown|unclear|not sure|misc(ellaneous)?)$/i;
export const isCatchAll = (label) => CATCH_ALL.test(String(label ?? "").trim());
// Labels beyond the four category slots share neutral marks, never another
// category's hue; names, tooltips and small multiples still expose each label
// separately with unchanged counts/actions.
export const OTHER_CATEGORY_COLOR = lightPalette.text.disabled;

// Boolean answers use two fixed brand slots, not success/failure colours:
// "No" is not necessarily bad ("Did the user ask for a human?"), so Yes is the
// brand purple and No the guide's amber.
export const YES_COLOR = lightPalette.dataBrand[0];
export const NO_COLOR = lightPalette.dataBrand[3];
export const YES_COLOR_DARK = darkPalette.dataBrand[0];
export const NO_COLOR_DARK = darkPalette.dataBrand[3];

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

// One answer's color: Yes in purple, No in amber (each mode's own step),
// categories by position, failures and skips muted.
export function answerColor(item, answer, theme) {
  if (answer === ERROR_ANSWER)
    return alpha(theme.palette.status.danger.solid, 0.45);
  if (answer === SKIPPED_ANSWER)
    return alpha(theme.palette.status.neutral.solid, 0.45);
  if (item.answerType === ANSWER_TYPES.CATEGORY)
    return categoryColor(
      item.labels.indexOf(answer),
      theme.palette.mode,
      answer,
    );
  const dark = theme.palette.mode === "dark";
  if (answer === "Yes") return dark ? YES_COLOR_DARK : YES_COLOR;
  return dark ? NO_COLOR_DARK : NO_COLOR;
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
