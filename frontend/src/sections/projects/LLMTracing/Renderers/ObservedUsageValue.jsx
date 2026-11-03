import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";

// Capture uncertainty is independent of pricing. A known observed zero remains
// zero, but it must not be presented as a complete/free turn or no activity.
export default function ObservedUsageValue({ coverage, value }) {
  const count = coverage.request_count;
  const label = `Observed-only · ${value}`;
  const explanation = `Capture coverage unknown. ${count} observed request${count === 1 ? "" : "s"}. Source summaries are nonadditive; observed amounts do not prove complete capture.`;
  return (
    <Tooltip title={`${label}. ${explanation}`} arrow placement="bottom">
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          width: "100%",
          minWidth: 0,
          boxSizing: "border-box",
          overflow: "hidden",
          px: 1.5,
        }}
      >
        <Typography
          variant="s1"
          noWrap
          sx={{
            minWidth: 0,
            maxWidth: "100%",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {label}
        </Typography>
      </Box>
    </Tooltip>
  );
}

ObservedUsageValue.propTypes = {
  coverage: PropTypes.shape({ request_count: PropTypes.number.isRequired })
    .isRequired,
  value: PropTypes.string.isRequired,
};
