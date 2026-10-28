import PropTypes from "prop-types";
import { Alert, Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SoonChip from "../../../../components/SoonChip";

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// Scenario verdict changes against the previous completed run, as the
// analytics payload's `comparison` reports them. The pass-rate and cost
// deltas Vel's banner carries aren't in that payload yet.
export default function RegressionBanner({ comparison, previousLabel }) {
  if (!comparison?.available) return null;
  const passing = comparison.newly_passing || [];
  const failing = comparison.newly_failing || [];
  // With no scenario evaluated in both runs there is nothing to call a change.
  const tone = !comparison.shared_scenarios
    ? {
        severity: "info",
        verdict: "No comparable scenarios",
        icon: "question-circle",
      }
    : failing.length && !passing.length
      ? { severity: "error", verdict: "Regressed", icon: "arrow-down" }
      : passing.length && !failing.length
        ? { severity: "success", verdict: "Improved", icon: "arrow-up" }
        : passing.length
          ? {
              severity: "warning",
              verdict: "Mixed changes",
              icon: "transfer-vertical",
            }
          : { severity: "info", verdict: "No change", icon: "minus-circle" };
  const names = (list) => (list.length ? list.join(", ") : "None");
  return (
    <Alert
      severity={tone.severity}
      icon={<Iconify icon={`solar:${tone.icon}-linear`} width={16} />}
      sx={{ "& .MuiAlert-message": { flex: 1, minWidth: 0 } }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography typography="s2" sx={{ fontWeight: 600 }}>
          {tone.verdict} vs {previousLabel || "the previous run"}
        </Typography>
        <SoonChip
          label="Pass rate delta"
          what="The pass-rate delta"
          size="tiny"
        />
        <SoonChip
          label="Cost delta"
          what="The cost-per-call delta"
          size="tiny"
        />
      </Stack>
      <CustomTooltip
        show
        arrow
        size="small"
        title={
          <Box>
            <div>Newly passing: {names(passing)}</div>
            <div>Regressed: {names(failing)}</div>
          </Box>
        }
      >
        <Typography
          typography="s3"
          sx={{ color: "text.secondary", mt: 0.25, width: "fit-content" }}
        >
          {plural(passing.length, "scenario")} newly passing · {failing.length}{" "}
          regressed · compared on{" "}
          {plural(comparison.shared_scenarios ?? 0, "scenario")}
        </Typography>
      </CustomTooltip>
    </Alert>
  );
}
RegressionBanner.propTypes = {
  comparison: PropTypes.shape({
    available: PropTypes.bool,
    shared_scenarios: PropTypes.number,
    newly_passing: PropTypes.arrayOf(PropTypes.string),
    newly_failing: PropTypes.arrayOf(PropTypes.string),
  }),
  previousLabel: PropTypes.string,
};
