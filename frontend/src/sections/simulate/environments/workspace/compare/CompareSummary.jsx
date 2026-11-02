import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SoonChip from "../../components/SoonChip";
import { RunLetter } from "./RunPills";
import { agentText } from "./compareFormat";
import { readPair, summaryLines } from "./compareStats";

const LABEL_SX = {
  px: 2.5,
  py: 1.5,
  typography: "s2",
  color: "text.secondary",
};

const pairLine = (read) =>
  [
    `${read.counts.paired} compared`,
    read.counts.regressed && `${read.counts.regressed} passed → failed`,
    read.counts.improved && `${read.counts.improved} failed → passed`,
    read.counts.new && `${read.counts.new} new`,
    read.counts.removed && `${read.counts.removed} removed`,
  ]
    .filter(Boolean)
    .join(" · ");

const SOON_LINES = [
  {
    id: "saidNotDone",
    label: "Said, not done",
    what: "Claims the call log doesn't support",
    plural: true,
  },
  { id: "cost", label: "Cost", what: "Cost per call" },
];

/**
 * Totals per run, read against the baseline: the question "is this version
 * better", without reference to any single scenario.
 */
export default function CompareSummary({ comparison, runs }) {
  const runIds = runs.map((r) => r.id);
  const columns = `220px repeat(${runs.length}, 1fr)`;
  const lines = summaryLines(comparison, runIds);

  return (
    <Box data-testid="compare-summary" sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 220 + runs.length * 180 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: columns,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box />
          {runs.map((run, i) => (
            <Stack
              key={run.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ px: 2, py: 1.5 }}
            >
              <RunLetter letter={run.letter} color={run.color} />
              <Box minWidth={0}>
                <Typography
                  noWrap
                  sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
                >
                  {run.label}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  {i === 0 ? `${agentText(run)} · baseline` : agentText(run)}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Box>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: columns,
            alignItems: "center",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={LABEL_SX}>Pairs against the baseline</Typography>
          {runs.map((run, i) => {
            const read = i
              ? readPair(comparison.rows, runIds[0], run.id)
              : null;
            return (
              <Box key={run.id} sx={{ px: 2, py: 1.5 }}>
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: "fontWeightSemiBold",
                    color: read ? "text.primary" : "text.subtitle",
                  }}
                >
                  {read ? pairLine(read) : "-"}
                </Typography>
                {read &&
                  read.label.id !== "rows" &&
                  read.interval.delta != null && (
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {`${read.interval.delta > 0 ? "+" : ""}${read.interval.delta} pts · ${read.interval.lo} to ${read.interval.hi} · ${read.label.label}`}
                    </Typography>
                  )}
              </Box>
            );
          })}
        </Box>

        {lines.map((line) => (
          <Box
            key={line.id}
            data-testid="summary-line"
            sx={{
              display: "grid",
              gridTemplateColumns: columns,
              alignItems: "center",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={LABEL_SX}>{line.label}</Typography>
            {line.values.map((v) => (
              <Stack
                key={v.runId}
                direction="row"
                alignItems="baseline"
                spacing={0.75}
                sx={{ px: 2, py: 1.5 }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: "fontWeightSemiBold",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {v.text}
                </Typography>
                {v.moved && (
                  <Stack direction="row" alignItems="center" spacing={0.25}>
                    <Iconify
                      icon={
                        v.better
                          ? "solar:arrow-up-linear"
                          : "solar:arrow-down-linear"
                      }
                      width={12}
                      sx={{ color: v.better ? "success.main" : "error.main" }}
                    />
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: "fontWeightSemiBold",
                        color: v.better ? "success.main" : "error.main",
                      }}
                    >
                      {v.better ? "better" : "worse"}
                    </Typography>
                  </Stack>
                )}
              </Stack>
            ))}
          </Box>
        ))}

        {SOON_LINES.map((line) => (
          <Box
            key={line.id}
            data-testid="summary-line"
            sx={{
              display: "grid",
              gridTemplateColumns: columns,
              alignItems: "center",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ pr: 2 }}
            >
              <Typography sx={LABEL_SX}>{line.label}</Typography>
              <SoonChip
                label={line.label}
                what={line.what}
                plural={line.plural}
                size="tiny"
              />
            </Stack>
          </Box>
        ))}
      </Box>
    </Box>
  );
}
CompareSummary.propTypes = {
  comparison: PropTypes.object.isRequired,
  runs: PropTypes.array.isRequired,
};
