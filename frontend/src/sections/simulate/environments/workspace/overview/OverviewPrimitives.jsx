import PropTypes from "prop-types";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { Box, Typography } from "@mui/material";

// A group heading: 13/500 sentence case.
export function GroupHeading({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 500,
        color: "text.secondary",
        mb: 1,
      }}
    >
      {children}
    </Typography>
  );
}
GroupHeading.propTypes = { children: PropTypes.node };

// A single read-only fact in the strip under the title: a muted label over a
// value. Verbatim from the designer's OverviewPanel Fact.
export function Fact({ label, value, color }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <OverflowTooltip title={label}>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {label}
        </Typography>
      </OverflowTooltip>
      <OverflowTooltip title={value}>
        <Typography
          noWrap
          sx={{
            typography: "s1",
            fontWeight: "fontWeightMedium",
            color: color || "text.primary",
          }}
        >
          {value}
        </Typography>
      </OverflowTooltip>
    </Box>
  );
}
Fact.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  color: PropTypes.string,
};
