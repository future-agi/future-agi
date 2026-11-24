import PropTypes from "prop-types";
import { Box, Tooltip } from "@mui/material";
import StatusBadge from "src/components/status-badge";
import { noValueReasons } from "./copy";

// One per-unit outcome, shown the same way in the preview and on Drivers.
// Neutral on purpose: whether a match is good depends on the metric, so the
// pill shows the outcome, not a verdict. The marker keeps the three apart
// without colour: filled (counts), hollow (doesn't count), dash (no value).
const noValueMarker = (
  <Box
    component="span"
    aria-hidden="true"
    sx={{
      width: 6,
      height: 2,
      borderRadius: 1,
      flexShrink: 0,
      bgcolor: "text.secondary",
    }}
  />
);

export default function MetricOutcomeBadge({ outcome, label, reason }) {
  if (typeof outcome !== "boolean") {
    const why = reason
      ? noValueReasons[reason] || reason.replaceAll("_", " ")
      : "";
    return (
      <Tooltip title={why ? `No value: ${why}` : ""} describeChild>
        {/* StatusBadge holds no ref, so a span carries the tooltip. */}
        <Box
          component="span"
          tabIndex={why ? 0 : undefined}
          sx={{
            display: "inline-flex",
            maxWidth: "100%",
            borderRadius: 0.5,
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "text.primary",
              outlineOffset: 2,
            },
          }}
        >
          <StatusBadge
            status="unset"
            role="neutral"
            variant="badge"
            size="sm"
            icon={noValueMarker}
            label="No value"
          />
        </Box>
      </Tooltip>
    );
  }
  return (
    <StatusBadge
      status={outcome ? "done" : "pending"}
      role="neutral"
      variant="badge"
      size="sm"
      label={label}
    />
  );
}
MetricOutcomeBadge.propTypes = {
  outcome: PropTypes.bool,
  label: PropTypes.string,
  reason: PropTypes.string,
};
