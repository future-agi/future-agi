import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { creditsLabel } from "../_mock/metering";

/**
 * The estimate before an Epoch starts, and a warning when the budget is
 * below it: "Estimated 2,100 credits · about 10 min", then "Below the
 * estimate: it may stop after 3 of 4 candidates." Shown where SHOW_EPOCH_COST
 * is on (costFlags).
 */
export default function EpochCostLine({ estimate, spend, candidates }) {
  if (!estimate?.likely) return null;
  const over = estimate.likely > spend;
  const n = candidates || estimate.runs || 0;
  const k =
    n > 1
      ? Math.max(1, Math.min(n - 1, Math.floor(spend / (estimate.likely / n))))
      : 0;
  const num = { fontVariantNumeric: "tabular-nums" };
  return (
    <>
      <Typography
        sx={{ typography: "s3", color: "text.secondary", mt: 0.5, ...num }}
      >
        {`Estimated ${creditsLabel(estimate.likely)}${estimate.minutes ? ` · about ${estimate.minutes} min` : ""}`}
      </Typography>
      {over && (
        <Typography
          sx={{
            typography: "s3",
            color: "status.warning.fg",
            mt: 0.25,
            ...num,
          }}
        >
          {n > 1
            ? `Below the estimate: it may stop after ${k} of ${n} candidates. Raise it to ${creditsLabel(estimate.likely)} to try them all.`
            : `Below the estimate: it may stop before it finishes. Raise it to ${creditsLabel(estimate.likely)}.`}
        </Typography>
      )}
    </>
  );
}

EpochCostLine.propTypes = {
  /* { likely (USD), minutes, runs } */
  estimate: PropTypes.object,
  /* The budget, in USD. */
  spend: PropTypes.number,
  candidates: PropTypes.number,
};
