import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { improvementMetricsOf, minutesLabel } from "./improvementMetrics";

/*
  The three Improvements metrics, as a quiet strip above the list — the same
  shape as the agent page's numbers: a label, the figure, what it's over.
*/
function Metric({ label, value, sub, tip }) {
  const word = !/^[—\d]/.test(value);
  return (
    <Tooltip arrow title={tip} placement="bottom-start">
      <Stack sx={{ px: 2, py: 1.5, minWidth: 0, cursor: "default" }} spacing={0.5}>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
        <Typography noWrap sx={{ fontSize: word ? 18 : 22, fontWeight: 700, lineHeight: "28px", fontVariantNumeric: "tabular-nums", color: word ? "text.secondary" : "text.primary" }}>
          {value}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{sub}</Typography>
      </Stack>
    </Tooltip>
  );
}
Metric.propTypes = { label: PropTypes.string, value: PropTypes.string, sub: PropTypes.string, tip: PropTypes.string };

export default function ImprovementMetricsStrip({ rows }) {
  const m = improvementMetricsOf(rows);
  return (
    <Box
      sx={{
        flexShrink: 0, display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
        border: "1px solid", borderColor: "divider", borderRadius: 1.5,
        "& > *:not(:last-of-type)": { borderRight: "1px solid", borderColor: "divider" },
      }}
    >
      <Metric
        label="Acceptance rate"
        value={m.acceptance ? `${m.acceptance.rate}%` : "—"}
        sub={m.acceptance ? `${m.acceptance.n} of ${m.acceptance.of} finished improvements accepted` : "No improvement has finished yet"}
        tip="Finished improvements whose proposal someone accepted — shipped as a pull request, a draft or a new version."
      />
      <Metric
        label="Time to a fix to review"
        value={minutesLabel(m.time.prompt ?? m.time.code)}
        sub={m.time.prompt != null && m.time.code != null
          ? `Prompt fixes · code fixes take ${minutesLabel(m.time.code)}`
          : m.time.code != null ? "Median · code fixes" : m.time.prompt != null ? "Median · prompt fixes" : "No proposal yet"}
        tip="Median time from starting an improvement to a proposal ready for review, for prompt-level and code-level changes."
      />
      <Metric
        label="Fix survival at 30 days"
        value={m.survival ? `${m.survival.rate}%` : "—"}
        sub={m.survival ? `${m.survival.n} of ${m.survival.of} released fixes held for 30 days` : "No released fix has finished its 30-day watch"}
        tip="Released fixes that didn't come back in the 30 days after release."
      />
    </Box>
  );
}
ImprovementMetricsStrip.propTypes = { rows: PropTypes.array };
