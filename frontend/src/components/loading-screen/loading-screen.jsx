import React, { useMemo } from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { useLocation } from "react-router";
import { alpha, useTheme } from "@mui/material/styles";

import LoadingTemplate from "../../sections/workbench/LoadingTemplate";
import {
  RocketMascot,
  LOADING_MESSAGES,
  pickRandom,
} from "../rocket-mascot";

// ----------------------------------------------------------------------

export default function LoadingScreen({
  sx,
  variant = "rocket",
  compact = false,
  message: messageOverride,
  ...other
}) {
  const location = useLocation();
  const theme = useTheme();

  const message = useMemo(
    () => messageOverride || pickRandom(LOADING_MESSAGES),
    [messageOverride],
  );

  if (location?.state?.fromOption === "use-template") {
    return <LoadingTemplate sx={{ mt: "46px" }} />;
  }

  const baseSx = {
    px: 5,
    width: 1,
    flexGrow: 1,
    minHeight: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
    ...sx,
  };

  if (compact) {
    return (
      <Box sx={baseSx} role="status" aria-live="polite" aria-busy="true" {...other}>
        <OrbitDots />
      </Box>
    );
  }

  if (variant === "orbit") {
    return (
      <Box sx={baseSx} role="status" aria-live="polite" aria-busy="true" {...other}>
        <OrbitLoader />
        <Box sx={{ textAlign: "center", maxWidth: 420, mt: 1 }}>
          <Typography
            variant="subtitle1"
            color="text.primary"
            letterSpacing="0.02em"
          >
            Standing by
          </Typography>
          <Typography
            variant="caption"
            fontFamily="IBM Plex Mono, ui-monospace, monospace"
            color={alpha(theme.palette.text.secondary, 0.8)}
            mt={0.5}
            sx={{
              "&::before": { content: '"// "', color: "text.disabled" },
            }}
          >
            {message}
          </Typography>
        </Box>
      </Box>
    );
  }

  // Default: rocket mascot
  return (
    <Box sx={baseSx} role="status" aria-live="polite" aria-busy="true" {...other}>
      <RocketMascot variant="launching" size={140} />
      <Box sx={{ textAlign: "center", maxWidth: 420 }}>
        <Typography
          variant="m2"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
          lineHeight={1.3}
        >
          Preparing for liftoff
        </Typography>
        <Typography
          variant="body2"
          fontStyle="italic"
          color="text.secondary"
          mt={0.5}
        >
          {message}
        </Typography>
      </Box>
    </Box>
  );
}

LoadingScreen.propTypes = {
  sx: PropTypes.object,
  compact: PropTypes.bool,
  variant: PropTypes.oneOf(["rocket", "orbit"]),
  message: PropTypes.string,
};

// ---------------------------------------------------------------------------
// Futuristic orbital loader — used by `variant="orbit"` and exported for reuse.
// ---------------------------------------------------------------------------

export function OrbitLoader({ size = 120 }) {
  const theme = useTheme();
  const primary = theme.palette.primary.main;
  const faint = alpha(theme.palette.text.primary, 0.12);

  return (
    <Box
      sx={{
        position: "relative",
        width: size,
        height: size,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        "@media (prefers-reduced-motion: reduce)": {
          "& .fagi-orbit-svg animateTransform, & .fagi-orbit-svg animate": {
            display: "none",
          },
        },
      }}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 120 120"
        fill="none"
        style={{ position: "absolute", inset: 0 }}
        className="fagi-orbit-svg"
      >
        {/* Static inner ring */}
        <circle cx="60" cy="60" r="22" stroke={faint} strokeWidth="1" />

        {/* Middle orbit — dashes rotating */}
        <g>
          <circle
            cx="60"
            cy="60"
            r="40"
            stroke={primary}
            strokeWidth="1.5"
            strokeDasharray="6 10"
            opacity="0.7"
          />
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 60 60"
            to="360 60 60"
            dur="3s"
            repeatCount="indefinite"
          />
        </g>

        {/* Outer orbit — rotating the opposite way */}
        <g>
          <circle
            cx="60"
            cy="60"
            r="54"
            stroke={faint}
            strokeWidth="1"
            strokeDasharray="2 6"
          />
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="360 60 60"
            to="0 60 60"
            dur="9s"
            repeatCount="indefinite"
          />
        </g>

        {/* Traveling satellite dot on the middle orbit */}
        <g>
          <circle cx="100" cy="60" r="3" fill={primary}>
            <animate
              attributeName="opacity"
              values="0.6;1;0.6"
              dur="1.2s"
              repeatCount="indefinite"
            />
          </circle>
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 60 60"
            to="360 60 60"
            dur="2s"
            repeatCount="indefinite"
          />
        </g>

        {/* Second satellite on outer orbit */}
        <g>
          <circle cx="60" cy="6" r="2" fill={primary} opacity="0.6" />
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 60 60"
            to="-360 60 60"
            dur="4.5s"
            repeatCount="indefinite"
          />
        </g>
      </svg>

      {/* Center pulse */}
      <Box
        sx={{
          width: 14,
          height: 14,
          borderRadius: "50%",
          bgcolor: primary,
          boxShadow: `0 0 16px ${alpha(primary, 0.7)}`,
          animation: "fagi-orbit-pulse 1.8s ease-in-out infinite",
          "@keyframes fagi-orbit-pulse": {
            "0%, 100%": { transform: "scale(1)", opacity: 0.9 },
            "50%": { transform: "scale(1.25)", opacity: 1 },
          },
          "@media (prefers-reduced-motion: reduce)": {
            animation: "none",
          },
        }}
      />
    </Box>
  );
}

OrbitLoader.propTypes = {
  size: PropTypes.number,
};

// Small inline version used by `compact` and anywhere a quick dot pulse is wanted.
export function OrbitDots() {
  const theme = useTheme();
  const color = theme.palette.primary.main;
  return (
    <Box
      sx={{
        display: "flex",
        gap: "6px",
        "& span": {
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: color,
          animation: "fagi-orbit-dot 1s ease-in-out infinite",
        },
        "& span:nth-of-type(2)": { animationDelay: "0.15s" },
        "& span:nth-of-type(3)": { animationDelay: "0.3s" },
        "@keyframes fagi-orbit-dot": {
          "0%, 100%": { transform: "translateY(0)", opacity: 0.5 },
          "50%": { transform: "translateY(-4px)", opacity: 1 },
        },
        "@media (prefers-reduced-motion: reduce)": {
          "& span": { animation: "none" },
        },
      }}
    >
      <span />
      <span />
      <span />
    </Box>
  );
}
