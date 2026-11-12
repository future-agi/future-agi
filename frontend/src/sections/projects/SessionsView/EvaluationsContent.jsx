import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import EvalStatusIndicator from "src/components/eval/EvalStatusIndicator";
import {
  normalizeEvalCellValue,
  extractScore,
  extractChoiceLabel,
} from "src/sections/develop-detail/DataTab/common";
import {
  EVAL_STATUS,
  getEvalNonScoreStatusFromValue,
  getEvalStatusLabel,
} from "src/utils/evalStatus";

// Screen-reader only: the running skeleton has no visible words.
const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

const getEvaluationMetricColor = (normalized) => {
  const numericValue = extractScore(normalized);
  // No usable score is not a pass: keep the "—" neutral instead of green.
  if (Number.isNaN(numericValue)) {
    return "text.secondary";
  }
  if (numericValue < 50) {
    return "status.danger.fg";
  }
  return "status.success.fg";
};

// Evals with no completed score arrive as `score: null` plus `error: true` or a
// lifecycle `status` (pending / running / skipped). They render through the
// shared indicator, the same as the Traces grid and the drawer's Evals tab.
const getMetricIndicatorStatus = (metric) =>
  metric?.error === true || metric?.status === EVAL_STATUS.ERRORED
    ? EVAL_STATUS.ERRORED
    : getEvalNonScoreStatusFromValue(metric);

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
          const indicatorStatus = getMetricIndicatorStatus(metric);
          if (indicatorStatus) {
            return (
              <Stack
                key={index}
                direction={"row"}
                alignItems={"center"}
                gap={0.5}
                sx={{ py: 0.5 }}
              >
                <Typography
                  typography="s2"
                  fontWeight={"fontWeightMedium"}
                  sx={{ color: "text.primary" }}
                >{`${metric.name}:`}</Typography>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    minWidth: 96,
                    height: 24,
                  }}
                >
                  <EvalStatusIndicator
                    status={indicatorStatus}
                    skippedReason={metric.skipped_reason}
                  />
                  {indicatorStatus === EVAL_STATUS.RUNNING && (
                    <Box component="span" sx={visuallyHidden}>
                      {getEvalStatusLabel(indicatorStatus)}
                    </Box>
                  )}
                </Box>
              </Stack>
            );
          }
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
