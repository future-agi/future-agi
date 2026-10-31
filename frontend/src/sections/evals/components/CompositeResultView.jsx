import { Box, Chip, Divider, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CellMarkdown from "src/sections/common/CellMarkdown";
import {
  getLabel,
  evalResultRole,
} from "src/sections/develop-detail/DataTab/common";
import StatusBadge from "src/components/status-badge/StatusBadge";

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
          {aggregationEnabled && aggregateScore != null ? (
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
                  <StatusBadge status={aggregatePass ? "passed" : "failed"} />
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
            label={`${completedChildren}/${totalChildren} completed`}
            color="default"
          />
          {failedChildren > 0 && (
            <StatusBadge
              label={`${failedChildren} failed`}
              role="danger"
              dot={false}
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
        {children.map((child) => {
          return (
            <Box
              key={child.child_id}
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
                  mb: child.reason || child.error ? 0.75 : 0,
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
                    #{child.order + 1}
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {child.child_name}
                  </Typography>
                  {child.weight != null && child.weight !== 1 && (
                    <Chip
                      size="small"
                      label={`w: ${child.weight}`}
                      variant="outlined"
                    />
                  )}
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                  {child.score != null && (
                    <Typography
                      component="span"
                      variant="s2"
                      sx={{
                        color: "text.primary",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {child.score.toFixed(3)}
                    </Typography>
                  )}
                  {child.output != null && child.score == null && (
                    <StatusBadge
                      label={getLabel(child.output)}
                      role={evalResultRole(child.output)}
                      dot={false}
                    />
                  )}
                  <StatusBadge status={child.status} />
                </Box>
              </Box>
              {child.reason && (
                <Box
                  sx={{
                    mt: 0.5,
                    "& div, pre": { whiteSpace: "pre-wrap" },
                  }}
                >
                  <CellMarkdown fontSize={12} text={child.reason} />
                </Box>
              )}
              {child.error && (
                <Typography
                  variant="caption"
                  color="error"
                  sx={{ display: "block", mt: 0.5 }}
                >
                  Error: {child.error}
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
            <CellMarkdown fontSize={12} text={summary} />
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
