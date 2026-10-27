import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

// A group heading, as Vel's current prototype sets it: sentence case, 13/600.
export function GroupHeading({ children }) {
  return (
    <Typography
      sx={{
        fontSize: 13,
        lineHeight: "20px",
        fontWeight: 600,
        color: "text.primary",
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
      {typeof value === "string" || typeof value === "number" ? (
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: color || "text.primary" }}
        >
          {value}
        </Typography>
      ) : (
        value
      )}
    </Box>
  );
}
Fact.propTypes = { label: PropTypes.string, value: PropTypes.node, color: PropTypes.string };
