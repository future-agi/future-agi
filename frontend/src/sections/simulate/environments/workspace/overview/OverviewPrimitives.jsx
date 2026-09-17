import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

// An uppercase group divider heading. Verbatim from the designer's OverviewPanel
// GroupHeading, retyped with a token fontWeight.
export function GroupHeading({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: "fontWeightBold",
        color: "text.primary",
        textTransform: "uppercase",
        letterSpacing: 0.5,
        mb: 1.25,
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
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: color || "text.primary" }}
      >
        {value}
      </Typography>
    </Box>
  );
}
Fact.propTypes = { label: PropTypes.string, value: PropTypes.node, color: PropTypes.string };
