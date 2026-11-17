import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// Plain-language reason a usage figure may be partial. Shared by the list cells
// and the session drawer so the explanation reads the same everywhere.
export function observedUsageExplanation(requestCount) {
  const received = requestCount
    ? `Counts only the ${requestCount.toLocaleString()} model call${requestCount === 1 ? "" : "s"} we received.`
    : "We haven't received any model calls.";
  return `${received} The agent's own run summary isn't added, so the real total may be higher.`;
}

// Capture uncertainty is independent of pricing. A known observed zero remains
// zero, but it must not be presented as a complete/free turn or no activity.
export default function ObservedUsageValue({ coverage, value }) {
  const explanation = observedUsageExplanation(coverage.request_count);
  return (
    <Tooltip title={`${value}. ${explanation}`} arrow placement="bottom">
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
          gap: 0.5,
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
          {`${value}*`}
        </Typography>
        <Iconify
          icon="solar:question-circle-linear"
          width={14}
          color="text.secondary"
          sx={{ flexShrink: 0 }}
        />
      </Box>
    </Tooltip>
  );
}

ObservedUsageValue.propTypes = {
  coverage: PropTypes.shape({ request_count: PropTypes.number.isRequired })
    .isRequired,
  value: PropTypes.string.isRequired,
};
