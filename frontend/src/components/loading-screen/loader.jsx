import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";

// Simple loader: one small spinner centred in the space the content will
// fill. Used wherever a skeleton could not copy the real layout — a generic
// skeleton reads as broken content, a spinner just reads as "loading".
//
// It fades in after a moment, so a fast load never flashes it, and holds
// still when the person prefers reduced motion.

export default function Loader({
  label = "Loading",
  role = "status",
  minHeight = 160,
  sx,
  ...other
}) {
  return (
    <Box
      role={role}
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
      sx={{
        width: 1,
        flexGrow: 1,
        minHeight,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        // Hidden only by the animation's first frame (fill: backwards), so the
        // element itself is never opacity 0.
        animation: "loader-in 0.2s ease-out 0.3s backwards",
        "@keyframes loader-in": { from: { opacity: 0 } },
        "@media (prefers-reduced-motion: reduce)": {
          "& .MuiCircularProgress-root, & .MuiCircularProgress-circle": {
            animation: "none",
          },
        },
        ...sx,
      }}
      {...other}
    >
      <CircularProgress
        size={20}
        thickness={4}
        aria-hidden
        sx={{ color: "text.secondary" }}
      />
    </Box>
  );
}

Loader.propTypes = {
  label: PropTypes.string,
  role: PropTypes.string,
  minHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  sx: PropTypes.object,
};
