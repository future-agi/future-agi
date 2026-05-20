import { Chip, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";
const extractScore = (value) => {
  const raw = value?.score;
  const normalized = normalizeEvalCellValue(raw);
  if (typeof normalized === "number") return normalized;
  if (normalized && typeof normalized === "object" && typeof normalized.score === "number") {
    return normalized.score;
  }
  return parseFloat(normalized);
};

const extractChoiceLabel = (value) => {
  const normalized = normalizeEvalCellValue(value?.score);
  if (normalized && typeof normalized === "object" && !Array.isArray(normalized)) {
    if (Array.isArray(normalized.choices)) return normalized.choices.join(", ");
    if (Array.isArray(normalized.choice)) return normalized.choice.join(", ");
    if (typeof normalized.choices === "string") return normalized.choices;
    if (typeof normalized.choice === "string") return normalized.choice;
  }
  return null;
};

const getEvaluationMetricColor = (value) => {
  const numericValue = extractScore(value);
  if (numericValue < 50) {
    return { backgroundColor: "red.o10", borderColor: "red.500" };
  }
  return { backgroundColor: "green.o10", borderColor: "green.500" };
};

export default function EvaluationsContent({ evaluationMetrics = {} }) {
  const hasEvaluations = Object.keys(evaluationMetrics).length > 0;

  return (
    <Stack
      direction={"column"}
      sx={{
        overflow: "auto",
        paddingY: 2,
      }}
      alignItems={"flex-start"}
    >
      {!hasEvaluations ? (
        <Typography
          variant="body2"
          sx={{
            color: "text.secondary",
            fontStyle: "italic",
            padding: "8px",
          }}
        >
          No evaluations available
        </Typography>
      ) : (
        Object.keys(evaluationMetrics).map((key, index) => {
          const metric = evaluationMetrics[key];
          const { backgroundColor, borderColor } =
            getEvaluationMetricColor(metric);
          const numericScore = extractScore(metric);
          const choiceLabel = extractChoiceLabel(metric);
          const scoreText = choiceLabel
            ? choiceLabel
            : isNaN(numericScore)
              ? "—"
              : `${numericScore}%`;
          return (
            <Chip
              key={index}
              label={
                <Typography
                  typography="s2"
                  sx={{ color: borderColor }}
                  fontWeight={"fontWeightMedium"}
                >{`${metric.name}: ${scoreText}`}</Typography>
              }
              sx={{
                backgroundColor: backgroundColor,
                height: "24px",
                borderRadius: "8px",
                margin: "4px",
                padding: "4px",
                "&:hover": {
                  backgroundColor,
                  borderColor,
                },
              }}
            />
          );
        })
      )}
    </Stack>
  );
}

EvaluationsContent.propTypes = {
  evaluationMetrics: PropTypes.object,
};
