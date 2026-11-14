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
//
// index.html draws the same splash (#boot-splash) on the first frame. While it
// is up, every SplashScreen keeps it instead of drawing its own, so the auth,
// deployment and redirect checks read as one splash. It goes once no
// SplashScreen is mounted (see releaseBootSplash).
// ---------------------------------------------------------------------------

const SHOW_AFTER_MS = 300;

let mountedSplashes = 0;
let removeTimer = null;

const bootSplash = () => document.getElementById("boot-splash");

// Removes the boot splash on the next frame unless another SplashScreen
// mounts first (one gate handing over to the next in the same commit).
export function releaseBootSplash() {
  if (mountedSplashes > 0 || !bootSplash()) return;
  cancelAnimationFrame(removeTimer);
  removeTimer = requestAnimationFrame(() => {
    if (mountedSplashes === 0) bootSplash()?.remove();
  });
}

export default function SplashScreen({ sx, ...other }) {
  const [holdsBoot] = useState(() => Boolean(bootSplash()));
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    mountedSplashes += 1;
    cancelAnimationFrame(removeTimer);
    return () => {
      mountedSplashes -= 1;
      releaseBootSplash();
    };
  }, []);

  useEffect(() => {
    if (holdsBoot) return undefined;
    const id = setTimeout(() => setVisible(true), SHOW_AFTER_MS);
    return () => clearTimeout(id);
  }, [holdsBoot]);

  if (holdsBoot || !visible) return null;

  return (
    <Box
      role="status"
      aria-live="polite"
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
