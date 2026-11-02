import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import SoonChip from "../../../components/SoonChip";

const NUM = { fontVariantNumeric: "tabular-nums" };

// The latest finished run's pass rate as one number, and how far it moved
// from the run before it. The pass rate is per call (passed / total), the
// same number the runs table prints.
export default function RunsHeadline({ latest, previous }) {
  if (!latest) {
    return (
      <Typography
        sx={{
          typography: "s1",
          fontWeight: "fontWeightSemiBold",
          flex: "1 0 auto",
        }}
      >
        Pass rate by run
      </Typography>
    );
  }
  const pts = previous ? latest.passRate - previous.passRate : null;

  return (
    <Stack
      data-testid="runs-headline"
      direction="row"
      alignItems="baseline"
      spacing={1.25}
      // Never squeezed: the eval chips wrap under it instead.
      sx={{ flex: "1 0 auto", minWidth: 0 }}
    >
      <Typography
        sx={{
          typography: "m1",
          lineHeight: 1,
          fontWeight: "fontWeightSemiBold",
          ...NUM,
        }}
      >
        {latest.passRate}
        <Box
          component="span"
          sx={{
            typography: "s2_1",
            fontWeight: "fontWeightMedium",
            color: "text.subtitle",
            ml: 0.25,
          }}
        >
          %
        </Box>
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
      >
        {`pass rate · ${latest.label}`}
        {pts != null && (
          <CustomTooltip
            show
            arrow
            size="small"
            title={<DeltaTip latest={latest} previous={previous} />}
          >
            <Box
              component="span"
              sx={{ ml: 1, cursor: "default", color: "text.primary" }}
            >
              {pts !== 0 && (
                <Box
                  component="span"
                  sx={{
                    color: pts < 0 ? "error.main" : "text.secondary",
                    typography: "s2",
                    mr: 0.5,
                  }}
                >
                  {pts < 0 ? "▼" : "▲"}
                </Box>
              )}
              {`${Math.abs(pts)} pts vs ${previous.label}`}
            </Box>
          </CustomTooltip>
        )}
      </Typography>
    </Stack>
  );
}

const runShape = PropTypes.shape({
  label: PropTypes.string,
  passRate: PropTypes.number,
  passed: PropTypes.number,
  tasks: PropTypes.number,
});

RunsHeadline.propTypes = {
  // Summary rows (summaryData.buildSummaryRow) of the latest finished run and
  // the finished run before it.
  latest: runShape,
  previous: runShape,
};

// The two runs' own counts. How far two runs of that size differ by chance
// needs per-scenario results the runs list doesn't send, so it is a soon chip.
function DeltaTip({ latest, previous }) {
  return (
    <Stack spacing={0.75} sx={{ py: 0.25 }}>
      <Typography sx={{ typography: "s2", ...NUM }}>
        {`${latest.label}: ${latest.passed} of ${latest.tasks} calls passed.`}
      </Typography>
      <Typography sx={{ typography: "s2", ...NUM }}>
        {`${previous.label}: ${previous.passed} of ${previous.tasks} calls passed.`}
      </Typography>
      <Box>
        <SoonChip
          label="Noise"
          what="The noise band between two runs"
          size="tiny"
        />
      </Box>
    </Stack>
  );
}

DeltaTip.propTypes = { latest: runShape, previous: runShape };
