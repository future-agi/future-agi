import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { creditsForUsd } from "../_mock/metering";

/* The range shown is at most 20% wide (Chintan, 25 Nov): 0.92× to 1.10×
   the likely cost — (1.10 − 0.92) / 0.92 ≈ 19.6%. */
const LOW = 0.92;
const HIGH = 1.1;
const round10 = (n) => Math.round(n / 10) * 10;

/**
 * The estimate before an Epoch starts, and a warning when the budget is
 * below it: "Estimated 1,660–1,980 credits · about 10 min" (never more than 20%
 * wide), then "Below the estimate: it may stop after 3 of 4 candidates." Shown where SHOW_EPOCH_COST
 * is on (costFlags).
 */
export default function EpochCostLine({ estimate, spend, candidates }) {
  if (!estimate?.likely) return null;
  const lowUsd = estimate.likely * LOW;
  const highUsd = estimate.likely * HIGH;
  const low = round10(creditsForUsd(lowUsd));
  const high = round10(creditsForUsd(highUsd));
  /* Below the top of the range, it may stop before every candidate runs. */
  const over = spend < highUsd;
  const n = candidates || estimate.runs || 0;
  const k =
    n > 1 ? Math.max(1, Math.min(n - 1, Math.floor(spend / (highUsd / n)))) : 0;
  const num = { fontVariantNumeric: "tabular-nums" };
  return (
    <>
      <Typography
        sx={{ typography: "s3", color: "text.secondary", mt: 0.5, ...num }}
      >
        {`Estimated ${low.toLocaleString()}–${high.toLocaleString()} credits${estimate.minutes ? ` · about ${estimate.minutes} min` : ""}`}
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
          {n > 1 && k < n
            ? `Below the estimate: it may stop after ${k} of ${n} candidates. Raise it to ${high.toLocaleString()} credits to try them all.`
            : `Below the top of the estimate: it may stop before it finishes. Raise it to ${high.toLocaleString()} credits.`}
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
