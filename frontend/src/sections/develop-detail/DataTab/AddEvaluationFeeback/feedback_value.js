import {
  extractChoiceLabel,
  normalizeEvalCellValue,
} from "src/sections/develop-detail/DataTab/common";

export const FEEDBACK_OUTPUT_TYPES = {
  REASON: "reason",
  SCORE: "score",
  PASS_FAIL: "Pass/Fail",
  CHOICES: "choices",
  SELECT: "select",
};

export const toArray = (value) => {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      /* fall through */
    }
    return value ? [value] : [];
  }
  return value === null || value === undefined ? [] : [value];
};

export const serializeFeedbackValue = (value) =>
  Array.isArray(value) ? JSON.stringify(value) : value;

export const getCurrentValue = (data, choiceScores) => {
  const raw = data?.value;
  if (raw === null || raw === undefined || raw === "") return "";
  const normalized = normalizeEvalCellValue(raw);
  let display;
  if (Array.isArray(normalized)) {
    display = normalized.map((v) => String(v)).join(", ");
  } else if (normalized && typeof normalized === "object") {
    // Score-with-choices evals emit {score, choice}; pull the label so
    // the drawer shows "Bad" instead of "[object Object]".
    display = extractChoiceLabel(normalized) ?? String(normalized ?? "");
  } else {
    display = String(normalized ?? "");
  }
  if (
    choiceScores &&
    typeof choiceScores === "object" &&
    display in choiceScores
  ) {
    return `${display} (score ${choiceScores[display]})`;
  }
  return display;
};

export const getReason = (data) => {
  let info = data?.valueInfos ?? data?.value_infos ?? {};
  if (typeof info === "string") {
    try {
      info = JSON.parse(info) || {};
    } catch {
      info = {};
    }
  }
  return info?.reason || info?.summary || data?.metadata?.reason || "";
};
