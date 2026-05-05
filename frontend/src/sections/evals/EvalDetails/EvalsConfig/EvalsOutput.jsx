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
            label={`${completedChildren}/${totalChildren} completed`}
            color="default"
          />
          {failedChildren > 0 && (
            <Chip
              size="small"
              label={`${failedChildren} failed`}
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
        {children.map((child) => {
          const statusColor = child.status === "failed" ? "error" : "default";
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
                      sx={{ height: 18, fontSize: "10px" }}
                    />
                  )}
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                  {child.score != null && (
                    <Chip
                      size="small"
                      label={child.score.toFixed(3)}
                      sx={{
                        ...getStatusColor(child.score, theme),
                        fontWeight: 600,
                      }}
                    />
                  )}
                  {child.output != null && child.score == null && (
                    <Chip
                      size="small"
                      label={getLabel(child.output)}
                      sx={getStatusColor(child.output, theme)}
                    />
                  )}
                  <Chip
                    size="small"
                    label={child.status}
                    color={statusColor}
                    sx={{ textTransform: "capitalize" }}
                  />
                </Box>
              </Box>
              {child.reason && (
                <Box
                  sx={{
                    mt: 0.5,
                    "& div, pre": { whiteSpace: "pre-wrap" },
                  }}
                >
                  <CellMarkdown fontSize={11} text={child.reason} />
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

      {/* Raw summary (debug/reference) */}
      {/* {summary && (
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

const EvalsOutput = ({ results }) => {
  const theme = useTheme();
  const compositeResult = results?.compositeResult;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: (theme) => theme.spacing(1),
        overflow: "auto",
        minHeight: "230px",
        height: "100%",
        position: "relative",
      }}
    >
      <Box
        sx={{
          position: "sticky",
          top: 0,
          width: "100%",
          zIndex: 1,
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: 1,
          }}
        >
          <Typography
            typography="s1"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            Result
          </Typography>
          {!compositeResult && (results?.output || results?.output == 0) && (
            <Chip
              size="small"
              label={getLabel(results?.output)}
              sx={{
                padding: "4px 8px",
                ...getStatusColor(results?.output, theme),
                transition: "none",
                "&:hover": {
                  backgroundColor: getStatusColor(results?.output, theme)
                    ?.backgroundColor, // Lock it to same color
                  boxShadow: "none",
                },
              }}
            />
          )}
        </Box>
        <Divider orientation="horizontal" />
      </Box>

      {compositeResult ? (
        <CompositeResultView compositeResult={compositeResult} />
      ) : (
        <Box
          sx={{
            minHeight: "calc(100% - 48px)",
            padding: 1,
            color: "text.primary",
            "& div, pre": {
              whiteSpace: "pre-wrap",
            },
          }}
        >
          {typeof results?.reason === "string" ? (
            <CellMarkdown fontSize={12} text={results.reason} />
          ) : (
            results?.reason?.map((item, index) => (
              <CellMarkdown key={index} fontSize={12} text={item} />
            ))
          )}
        </Box>
      )}
    </Box>
  );
};

export default EvalsOutput;

EvalsOutput.propTypes = {
  children: PropTypes.node,
  results: PropTypes.object,
};
