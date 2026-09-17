import React from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import { keyframes } from "@mui/system";

// Compact horizontal "live" banner: a little starship glides across a dashed
// trajectory while validation runs. Used in place of the tall vertical hero on
// the validation step so the check list has more vertical room.

const flow = keyframes`
  to { stroke-dashoffset: -30; }
`;

const twinkle = keyframes`
  0%, 100% { opacity: 0.2; }
  50%      { opacity: 0.9; }
`;

const STARS = [
  { left: "12%", top: "30%", d: 0 },
  { left: "34%", top: "68%", d: 0.8 },
  { left: "58%", top: "24%", d: 1.4 },
  { left: "76%", top: "62%", d: 0.4 },
  { left: "90%", top: "38%", d: 1.1 },
];

export default function HorizontalSpaceship({ progress = 0, height = 60, sx }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const LINE = isDark ? "#EAEFF6" : theme.palette.grey[700];
  const starColor = isDark
    ? theme.palette.common.white
    : theme.palette.grey[500];
  const p = Math.max(0, Math.min(1, progress));
  return (
    <Box
      aria-hidden
      sx={{
        position: "relative",
        width: "100%",
        height,
        overflow: "hidden",
        ...sx,
      }}
    >
      {/* Trajectory track */}
      <Box
        component="svg"
        viewBox="0 0 400 4"
        preserveAspectRatio="none"
        sx={{
          position: "absolute",
          left: 0,
          right: 0,
          top: "50%",
          width: "100%",
          height: 4,
        }}
      >
        <line
          x1="0"
          y1="2"
          x2="400"
          y2="2"
          stroke={LINE}
          strokeWidth="1"
          strokeOpacity="0.18"
          strokeDasharray="5 7"
          style={{ animation: `${flow} 2.4s linear infinite` }}
        />
      </Box>

      {/* Stars */}
      {STARS.map((s, i) => (
        <Box
          key={i}
          sx={{
            position: "absolute",
            left: s.left,
            top: s.top,
            width: 2,
            height: 2,
            borderRadius: "50%",
            bgcolor: starColor,
            animation: `${twinkle} ${2.2 + (i % 3) * 0.6}s ease-in-out ${s.d}s infinite`,
          }}
        />
      ))}

      {/* Ship — position tracks validation progress, resting at the end when done */}
      <Box
        sx={{
          position: "absolute",
          top: "50%",
          mt: "-14px",
          // Keep the whole 82px-wide ship inside the track so it isn't clipped
          // when it reaches the end.
          left: `calc(${p} * (100% - 84px))`,
          transition: "left 0.6s ease",
        }}
      >
        <Box
          component="svg"
          viewBox="-18 0 82 28"
          fill="none"
          stroke={LINE}
          strokeLinecap="round"
          strokeLinejoin="round"
          sx={{ width: 82, height: 28, overflow: "visible" }}
        >
          {/* Thrust trail */}
          <g
            style={{ animation: `${flow} 0.6s linear infinite` }}
            strokeWidth="1.3"
            strokeOpacity="0.5"
            strokeDasharray="3 4"
          >
            <path d="M4 12 L -16 10" />
            <path d="M4 14 L -18 14" />
            <path d="M4 16 L -16 18" />
          </g>
          {/* Fin */}
          <path d="M22 11 L 18 4 L 30 11 Z" strokeWidth="1.2" />
          {/* Wing */}
          <path d="M28 17 L 20 25 L 36 18 Z" strokeWidth="1.2" />
          {/* Fuselage */}
          <path
            d="M58 14 C 44 9, 28 9, 14 11 C 8 12, 5 13, 4 14 C 5 15, 8 16, 14 17 C 28 19, 44 19, 58 14 Z"
            strokeWidth="1.5"
          />
          {/* Cockpit */}
          <circle cx="44" cy="13.5" r="2.3" strokeWidth="1.1" />
          <circle cx="58" cy="14" r="1.6" fill={LINE} stroke="none" />
        </Box>
      </Box>
    </Box>
  );
}

HorizontalSpaceship.propTypes = {
  progress: PropTypes.number,
  height: PropTypes.number,
  sx: PropTypes.object,
};
