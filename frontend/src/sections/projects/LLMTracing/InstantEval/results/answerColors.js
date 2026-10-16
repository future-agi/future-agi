import { alpha } from "@mui/material/styles";
import { ANSWER_TYPES } from "../instantEvalPayloads";
import { ERROR_ANSWER, SKIPPED_ANSWER } from "../instantEvalAnswers";

// Categorical hues for category answers; the first is the brand accent.
export const CATEGORY_COLORS = [
  "#7857FC",
  "#0EA5E9",
  "#F59E0B",
  "#10B981",
  "#EF4444",
  "#EC4899",
  "#14B8A6",
  "#8B5CF6",
  "#F97316",
  "#64748B",
];

// Yes and No get two strong hues that judge neither: the brand purple and an
// amber. Not red (that means failed) and not green ("No" is not always bad:
// "Did the customer ask for a human?").
export const YES_COLOR = "#7857FC";
export const NO_COLOR = "#F97316";

// One answer's color: Yes in purple, No in amber, categories by position,
// failures and skips muted.
export function answerColor(item, answer, theme) {
  if (answer === ERROR_ANSWER) return alpha(theme.palette.error.main, 0.45);
  if (answer === SKIPPED_ANSWER) return theme.palette.grey[400];
  if (item.answerType === ANSWER_TYPES.CATEGORY) {
    const index = item.labels.indexOf(answer);
    return CATEGORY_COLORS[
      (index >= 0 ? index : item.labels.length) % CATEGORY_COLORS.length
    ];
  }
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
