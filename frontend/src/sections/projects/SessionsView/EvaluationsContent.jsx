import { Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import {
  normalizeEvalCellValue,
  extractScore,
  extractChoiceLabel,
} from "src/sections/develop-detail/DataTab/common";

const getEvaluationMetricColor = (normalized) => {
  const numericValue = extractScore(normalized);
  if (numericValue < 50) {
    return "status.danger.fg";
  }
  return "status.success.fg";
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
          const normalized = normalizeEvalCellValue(metric?.score);
          const scoreColor = getEvaluationMetricColor(normalized);
          const numericScore = extractScore(normalized);
          const choiceLabel = extractChoiceLabel(normalized);
          const scoreText = choiceLabel
            ? choiceLabel
            : isNaN(numericScore)
              ? "—"
              : `${numericScore}%`;
          return (
            <Typography
              key={index}
              typography="s2"
              fontWeight={"fontWeightMedium"}
              sx={{ color: scoreColor, py: 0.5 }}
            >{`${metric.name}: ${scoreText}`}</Typography>
          );
        })
      )}
    </Stack>
  );
}

EvaluationsContent.propTypes = {
  evaluationMetrics: PropTypes.object,
};
