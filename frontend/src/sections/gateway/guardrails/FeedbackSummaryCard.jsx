import React from "react";
import {
  Typography,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Skeleton,
  LinearProgress,
} from "@mui/material";
import Section from "src/components/section";
import { useGuardrailFeedbackSummary } from "./hooks/useGuardrailFeedback";

function formatPct(val) {
  if (val == null || isNaN(val)) return "0%";
  return `${(Number(val) * 100).toFixed(1)}%`;
}

function normalizeSummaryItem(item) {
  return {
    checkName: item.checkName ?? item.check_name ?? "",
    total: item.total ?? item.total_feedback ?? 0,
    correctCount: item.correctCount ?? item.correct_count ?? 0,
    falsePositiveCount:
      item.falsePositiveCount ?? item.false_positive_count ?? 0,
    falseNegativeCount:
      item.falseNegativeCount ?? item.false_negative_count ?? 0,
  };
}

const FeedbackSummaryCard = () => {
  const { data: summary, isLoading } = useGuardrailFeedbackSummary();

  if (isLoading) {
    return (
      <Section variant="panel">
        <Skeleton width={200} height={30} sx={{ mb: 2 }} />
        <Stack spacing={1}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} width="100%" height={36} />
          ))}
        </Stack>
      </Section>
    );
  }

  if (!summary?.length) {
    return (
      <Section variant="panel">
        <Typography variant="subtitle1" mb={1}>
          Feedback Summary
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          textAlign="center"
          py={3}
        >
          No feedback data yet. Submit feedback on guardrail results in the
          request log detail view.
        </Typography>
      </Section>
    );
  }

  return (
    <Section variant="panel">
      <Typography variant="subtitle1" mb={2}>
        Feedback Summary by Check
      </Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Check Name</TableCell>
              <TableCell
                sx={{ fontVariantNumeric: "tabular-nums" }}
                align="right"
              >
                Total
              </TableCell>
              <TableCell
                sx={{ fontVariantNumeric: "tabular-nums" }}
                align="right"
              >
                Correct
              </TableCell>
              <TableCell
                sx={{ fontVariantNumeric: "tabular-nums" }}
                align="right"
              >
                False Positive
              </TableCell>
              <TableCell
                sx={{ fontVariantNumeric: "tabular-nums" }}
                align="right"
              >
                False Negative
              </TableCell>
              <TableCell>Accuracy</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {summary.map((rawItem) => {
              const item = normalizeSummaryItem(rawItem);
              const accuracy =
                item.total > 0 ? item.correctCount / item.total : 0;
              return (
                <TableRow key={item.checkName} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={500}>
                      {item.checkName}
                    </Typography>
                  </TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    <Typography variant="body2">{item.total}</Typography>
                  </TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        fontVariantNumeric: "tabular-nums",
                        color: "status.success.fg",
                      }}
                    >
                      {item.correctCount || 0}
                    </Typography>
                  </TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        fontVariantNumeric: "tabular-nums",
                        color: "status.warning.fg",
                      }}
                    >
                      {item.falsePositiveCount || 0}
                    </Typography>
                  </TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        fontVariantNumeric: "tabular-nums",
                        color: "status.danger.fg",
                      }}
                    >
                      {item.falseNegativeCount || 0}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ width: 130 }}>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <LinearProgress
                        variant="determinate"
                        value={accuracy * 100}
                        color={
                          accuracy > 0.8
                            ? "success"
                            : accuracy > 0.5
                              ? "warning"
                              : "error"
                        }
                        sx={{ flex: 1, height: 6, borderRadius: 3 }}
                      />
                      <Typography variant="caption" sx={{ minWidth: 36 }}>
                        {formatPct(accuracy)}
                      </Typography>
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Section>
  );
};

export default FeedbackSummaryCard;
