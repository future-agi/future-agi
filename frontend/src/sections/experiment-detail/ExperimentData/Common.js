import { palette } from "src/theme/palette";
import { MODEL_TYPES } from "../../develop-detail/RunPrompt/common";
import {
  normalizeEvalCellValue,
  extractChoiceLabel,
} from "src/sections/develop-detail/DataTab/common";

const statusColor = {
  Passed: "success",
  pass: "success",
  error: "error",
  Failed: "error",
};

export const parseArrayString = (value) => {
  const normalized = normalizeEvalCellValue(value);
  if (Array.isArray(normalized)) return normalized;
  if (typeof value === "string") {
    try {
      return JSON.parse(value.replace(/'/g, '"'));
    } catch {
      return [value];
    }
  }
  return [value];
};

export function interpolateColorforExperiment(
  score,
  maxScore = 10,
  reverse = false,
) {
  if (score < 0) score = 0;
  if (score > maxScore) score = maxScore;

  const factor = (score / maxScore) * 100;

  if (reverse) {
    if (factor <= 49) {
      return palette("light").green.o10;
    } else if (factor <= 79) {
      return palette("light").orange.o10;
    } else {
      return palette("light").red.o10;
    }
  } else {
    if (factor <= 49) {
      return palette("light").red.o10;
    } else if (factor <= 79) {
      return palette("light").orange.o10;
    } else {
      return palette("light").green.o10;
    }
  }
}

export function getChipTextColor(score, maxScore = 10, reverse = false) {
  if (score < 0) score = 0;
  if (score > maxScore) score = maxScore;

  const factor = (score / maxScore) * 100;

  if (reverse) {
    if (factor <= 49) return palette("light").green[500];
    if (factor <= 79) return palette("light").orange[500];
    return palette("light").red[500];
  } else {
    if (factor <= 49) return palette("light").red[500];
    if (factor <= 79) return palette("light").orange[500];
    return palette("light").green[500];
  }
}

export const getChipLabel = (data) => {
  const cellValue = data?.cellValue;

  if (!cellValue || cellValue === "error") return "error";

  if (data?.dataType === "boolean") {
    if (cellValue === "Failed") return "Failed";
    if (cellValue === "Passed") return "Passed";
    return cellValue;
  }

  // LLM evals may pass {score, choice} (object or Python-repr string) — unwrap.
  const normalized = normalizeEvalCellValue(cellValue);

  if (data?.dataType === "float") {
    const rawScore =
      normalized && typeof normalized === "object" && !Array.isArray(normalized)
        ? typeof normalized.score === "number"
          ? normalized.score
          : NaN
        : parseFloat(normalized);
    return isNaN(rawScore) ? "Error" : `${(rawScore * 100).toFixed(0)}%`;
  }

  if (
    normalized &&
    typeof normalized === "object" &&
    !Array.isArray(normalized)
  ) {
    const choiceLabel = extractChoiceLabel(normalized);
    if (choiceLabel != null) return choiceLabel;
    if (typeof normalized.score === "number") {
      return `${(normalized.score * 100).toFixed(0)}%`;
    }
    return JSON.stringify(normalized);
  }

  return normalized;
};

export const getChipColor = (data) => {
  if (data?.dataType === "boolean" || data?.cellValue === "error") {
    return statusColor[data?.cellValue] || "default";
  }
  if (data?.dataType === "array" || data?.dataType === "float")
    return "success";
  return "error";
};

const mod = (n, m) => ((n % m) + m) % m;

export const getColorByIndex = (index) => {
  const colors = [
    { bg: "primary.lighter", text: "primary.light" },
    { bg: "primary.lighter", text: "primary.dark" },
    { bg: "orange.100", text: "orange.400" },
    { bg: "orange.200", text: "orange.700" },
    { bg: "green.100", text: "green.400" },
    { bg: "green.200", text: "green.700" },
    { bg: "blue.100", text: "blue.400" },
    { bg: "blue.200", text: "blue.700" },
    { bg: "pink.100", text: "pink.400" },
    { bg: "pink.200", text: "pink.700" },
  ];
  return colors[mod(index, colors.length)];
};

export const commonBorder = {
  border: "1px solid",
  borderColor: "background.neutral",
  borderRadius: "8px",
};

export function toExcelLetters(num) {
  let s = "";
  while (num >= 0) {
    s = String.fromCharCode((num % 26) + 65) + s;
    num = Math.floor(num / 26) - 1;
  }
  return s;
}

export const shouldShowDiffModeButton = (experimentData) => {
  // Defensive: validate input
  if (!experimentData) return false;
  if (!experimentData?.columnId) return false;

  const isLLMType =
    experimentData.experimentType?.toLowerCase() === MODEL_TYPES.LLM;
  if (!isLLMType) return false;

  // Check for valid configurations
  const hasAgentConfigs =
    Array.isArray(experimentData?.agentConfigs) &&
    experimentData?.agentConfigs?.length > 0;

  const hasValidPromptConfigs =
    Array.isArray(experimentData?.promptConfigs) &&
    experimentData?.promptConfigs?.length > 0 &&
    experimentData?.promptConfigs[0]?.outputFormat === "string";

  return hasAgentConfigs || hasValidPromptConfigs;
};
