import React, { useMemo } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { GatewayEmptyState, GatewayGrid } from "../components/frame";
import { catalogEntry } from "./catalog";
import { useGuardrailFeedbackSummary } from "./hooks/useGuardrailFeedback";
import { EMPTY } from "../utils/formatters";

const n = (value) => Number(value || 0);

function toRow(item) {
  const name = item.check_name ?? item.checkName ?? "";
  const total = n(item.total_feedback ?? item.total);
  const correct = n(item.correct_count ?? item.correctCount);
  return {
    id: name,
    label: catalogEntry(name)?.label || name,
    total,
    correct,
    falsePositive: n(item.false_positive_count ?? item.falsePositiveCount),
    falseNegative: n(item.false_negative_count ?? item.falseNegativeCount),
    unsure: n(item.unsure_count ?? item.unsureCount),
    accuracy: total > 0 ? (correct / total) * 100 : null,
  };
}

const NUMBER = { flex: 0, width: 130, minWidth: 110, type: "rightAligned" };

const COLUMNS = [
  { field: "label", headerName: "Check", minWidth: 200, flex: 2 },
  { field: "total", headerName: "Reviews", ...NUMBER },
  { field: "correct", headerName: "Correct", ...NUMBER },
  {
    field: "falsePositive",
    headerName: "False positive",
    ...NUMBER,
    width: 170,
  },
  {
    field: "falseNegative",
    headerName: "False negative",
    ...NUMBER,
    width: 170,
  },
  { field: "unsure", headerName: "Unsure", ...NUMBER },
  {
    field: "accuracy",
    headerName: "Accuracy",
    headerTooltip: "Reviews marked correct, out of all reviews of this check",
    valueFormatter: ({ value }) =>
      value === null || value === undefined ? EMPTY : `${Math.round(value)}%`,
    ...NUMBER,
  },
];

/**
 * How reviewers judged each check's matches. A review is given on a matched
 * request's detail in the request logs.
 */
const FeedbackSummaryCard = () => {
  const summary = useGuardrailFeedbackSummary();
  const rows = useMemo(
    () => (Array.isArray(summary.data) ? summary.data.map(toRow) : undefined),
    [summary.data],
  );

  if (summary.isError) {
    return (
      <GatewayEmptyState
        icon="guardrails"
        title="Feedback could not be loaded"
        description="The feedback service did not answer. Reviews already given are kept."
        actionLabel="Retry"
        onAction={() => summary.refetch()}
      />
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Typography typography="s2" color="text.secondary">
        Reviewers mark each match as correct, a false positive, a false negative
        or unsure from the request&apos;s detail in Logs. Each reviewer has one
        answer per check on each request: an edited answer counts once.
      </Typography>
      <GatewayGrid
        rowData={rows}
        loading={summary.isLoading}
        columnDefs={COLUMNS}
        getRowId={(p) => p.data.id}
        stateKey="gateway-guardrail-feedback"
        height="auto"
        domLayout="autoHeight"
        emptyState={{
          icon: "guardrails",
          title: "No feedback recorded",
          description:
            "Open a matched request in Logs and mark whether the rule was right. Accuracy per check appears here.",
        }}
      />
    </Box>
  );
};

export default FeedbackSummaryCard;
