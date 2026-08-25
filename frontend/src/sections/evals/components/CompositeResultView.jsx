import { Box, Chip, Divider, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CellMarkdown from "src/sections/common/CellMarkdown";
import {
  getLabel,
  getStatusColor,
} from "src/sections/develop-detail/DataTab/common";

const AGGREGATION_LABELS = {
  weighted_avg: "Weighted Average",
  avg: "Average",
  min: "Minimum",
  max: "Maximum",
  pass_rate: "Pass Rate",
};

/**
 * Renders the result of executing a composite eval:
 *  - Aggregate score (if aggregation enabled)
 *  - Per-child cards (name, score, weight, reason, error)
 *  - Structured summary
 *
 * Used by both the revamped Eval Detail page playground and the legacy
 * playground drawer.
 */
const CompositeResultView = ({ compositeResult }) => {
  const theme = useTheme();
  const {
    aggregation_enabled: aggregationEnabled,
    aggregation_function: aggregationFunction,
    aggregate_score: aggregateScore,
    aggregate_pass: aggregatePass,
    children = [],
    summary,
    total_children: totalChildren,
    completed_children: completedChildren,
    failed_children: failedChildren,
  } = compositeResult || {};

  // Every field here comes from a stored JSON payload, so a partial or older
  // snapshot must degrade rather than throw.
  const childList = Array.isArray(children) ? children : [];
  const countBy = (status) =>
    childList.filter((child) => child?.status === status).length;
  const totalCount = Number.isFinite(totalChildren)
    ? totalChildren
    : childList.length;
  const completedCount = Number.isFinite(completedChildren)
    ? completedChildren
    : countBy("completed");
  const failedCount = Number.isFinite(failedChildren)
    ? failedChildren
    : countBy("failed");
  const hasAggregateScore = typeof aggregateScore === "number";

  return (
    <Box sx={{ p: 1.5 }}>
      {/* Header: aggregate score (if enabled) + counts */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 1.5,
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        <Box>
          {aggregationEnabled && hasAggregateScore ? (
            <>
              <Typography variant="caption" color="text.secondary">
                Aggregate Score (
                {AGGREGATION_LABELS[aggregationFunction] || aggregationFunction}
                )
              </Typography>
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Typography
                  variant="h5"
                  sx={{ fontWeight: 600, color: "text.primary" }}
                >
                  {aggregateScore.toFixed(3)}
                </Typography>
                {aggregatePass != null && (
                  <Chip
                    size="small"
                    label={aggregatePass ? "PASS" : "FAIL"}
                    color={aggregatePass ? "success" : "error"}
                    sx={{ fontWeight: 600 }}
                  />
                )}
              </Box>
            </>
          ) : (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ fontStyle: "italic" }}
            >
              {aggregationEnabled
                ? "No aggregate score (no children produced a normalized score)"
                : "Aggregation disabled — individual child results only"}
            </Typography>
          )}
        </Box>
        <Stack direction="row" spacing={1}>
          <Chip
            size="small"
            label={`${completedCount}/${totalCount} completed`}
            color="default"
          />
          {failedCount > 0 && (
            <Chip
              size="small"
              label={`${failedCount} failed`}
              color="error"
            />
          )}
        </Stack>
      </Box>

      <Divider sx={{ mb: 1.5 }} />

      {/* Per-child results */}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 1, fontWeight: 600 }}
      >
        Child Evaluations
      </Typography>
      <Stack spacing={1}>
        {childList.map((child, idx) => {
          const statusColor = child?.status === "failed" ? "error" : "default";
          const order = Number.isFinite(child?.order) ? child.order : idx;
          const hasScore = typeof child?.score === "number";
          return (
            <Box
              key={child?.child_id ?? idx}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                p: 1.25,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 1,
                  mb: child?.reason || child?.error ? 0.75 : 0,
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography
                    variant="caption"
                    sx={{
                      color: "primary.main",
                      fontWeight: 600,
                      minWidth: 20,
                    }}
                  >
                    #{order + 1}
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {child?.child_name || "Child"}
                  </Typography>
                  {typeof child?.weight === "number" &&
                    child.weight !== 1 && (
                    <Chip
                      size="small"
                      label={`w: ${child.weight}`}
                      variant="outlined"
                      sx={{ height: 18, fontSize: "10px" }}
                    />
                  )}
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                  {hasScore && (
                    <Chip
                      size="small"
                      label={child.score.toFixed(3)}
                      sx={{
                        ...getStatusColor(child.score, theme),
                        fontWeight: 600,
                      }}
                    />
                  )}
                  {child?.output != null && !hasScore && (
                    <Chip
                      size="small"
                      label={getLabel(child.output)}
                      sx={getStatusColor(child.output, theme)}
                    />
                  )}
                  <Chip
                    size="small"
                    label={child?.status || "unknown"}
                    color={statusColor}
                    sx={{ textTransform: "capitalize" }}
                  />
                </Box>
              </Box>
              {child?.reason && (
                <Box
                  sx={{
                    mt: 0.5,
                    "& div, pre": { whiteSpace: "pre-wrap" },
                  }}
                >
                  <CellMarkdown fontSize={11} text={child.reason} />
                </Box>
              )}
              {child?.error && (
                <Typography
                  variant="caption"
                  color="error"
                  sx={{ display: "block", mt: 0.5 }}
                >
                  Error: {String(child.error)}
                </Typography>
              )}
            </Box>
          );
        })}
      </Stack>
      {/* 
      {summary && (
        <Box sx={{ mt: 2 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5, fontWeight: 600 }}
          >
            Summary
          </Typography>
          <Box
            sx={{
              border: "1px dashed",
              borderColor: "divider",
              borderRadius: 1,
              p: 1,
              "& div, pre": { whiteSpace: "pre-wrap" },
            }}
          >
            <CellMarkdown fontSize={11} text={summary} />
          </Box>
        </Box>
      )} */}
    </Box>
  );
};

CompositeResultView.propTypes = {
  compositeResult: PropTypes.object,
};

export default CompositeResultView;
