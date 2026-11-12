import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import SvgColor from "../svg-color";

// ---------------------------------------------------------------------------
// Splash screen: the mark, the name and one thin indeterminate line.
//
// It waits a moment before showing, so a fast load never flashes it, and the
// line holds still when the person prefers reduced motion.
// ---------------------------------------------------------------------------

const SHOW_AFTER_MS = 300;

export default function SplashScreen({ sx, ...other }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setVisible(true), SHOW_AFTER_MS);
    return () => clearTimeout(id);
  }, []);

  if (!visible) return null;

  return (
    <Box
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label="Loading"
      sx={{
        right: 0,
        width: 1,
        bottom: 0,
        height: 1,
        zIndex: 9998,
        display: "flex",
        position: "absolute",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 1.5,
        bgcolor: "background.default",
        ...sx,
      }}
      {...other}
    >
      <SvgColor
        src="/favicon/logo.svg"
        sx={{ width: 32, height: 32, color: "primary.main" }}
      />
      <Typography variant="m1" fontWeight={600} color="text.primary">
        Future AGI
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Loading{"\u2026"}
      </Typography>
      <Box
        sx={{
          width: 96,
          height: 2,
          mt: 0.5,
          borderRadius: "2px",
          overflow: "hidden",
          bgcolor: "divider",
        }}
      >
        <Box
          sx={{
            width: "40%",
            height: 1,
            bgcolor: "primary.main",
            animation: "splash-line 1.4s ease-in-out infinite",
            "@keyframes splash-line": {
              "0%": { transform: "translateX(-100%)" },
              "100%": { transform: "translateX(250%)" },
            },
            "@media (prefers-reduced-motion: reduce)": {
              animation: "none",
              transform: "translateX(75%)",
            },
          }}
        />
      </Box>
    </Box>
  );
}

SplashScreen.propTypes = {
  sx: PropTypes.object,
};
