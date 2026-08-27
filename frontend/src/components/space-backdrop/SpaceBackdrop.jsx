import React from "react";
import { Box, useTheme } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { keyframes } from "@mui/system";

// Decorative starfield behind the auth screens and the self-hosted setup flow.
// Rendered in both themes: white stars with a glow read on the dark background,
// while light needs a grey-violet star and no glow or the field turns to soot.

const twinkle = keyframes`
  0%, 100% { opacity: 0.15; transform: scale(0.8); }
  50%      { opacity: 0.9;  transform: scale(1); }
`;

const shoot = keyframes`
  0%   { transform: translate3d(0, 0, 0) rotate(28deg); opacity: 0; }
  8%   { opacity: 1; }
  22%  { opacity: 1; }
  36%  { transform: translate3d(420px, 220px, 0) rotate(28deg); opacity: 0; }
  100% { opacity: 0; }
`;

// Deterministic star field — fixed so it renders identically every mount.
const STARS = [
  { top: "8%", left: "12%", s: 2, d: 0 },
  { top: "14%", left: "82%", s: 3, d: 1.2 },
  { top: "22%", left: "34%", s: 1.5, d: 2.1 },
  { top: "18%", left: "58%", s: 2, d: 0.6 },
  { top: "30%", left: "8%", s: 2.5, d: 1.8 },
  { top: "34%", left: "90%", s: 1.5, d: 0.3 },
  { top: "42%", left: "20%", s: 2, d: 2.6 },
  { top: "46%", left: "72%", s: 1.5, d: 1.1 },
  { top: "52%", left: "44%", s: 2.5, d: 0.9 },
  { top: "58%", left: "86%", s: 2, d: 2.3 },
  { top: "62%", left: "6%", s: 1.5, d: 1.5 },
  { top: "68%", left: "30%", s: 2, d: 0.4 },
  { top: "72%", left: "64%", s: 3, d: 1.9 },
  { top: "78%", left: "16%", s: 1.5, d: 2.8 },
  { top: "82%", left: "88%", s: 2, d: 0.7 },
  { top: "88%", left: "48%", s: 2.5, d: 1.4 },
  { top: "10%", left: "46%", s: 1.5, d: 2.0 },
  { top: "26%", left: "68%", s: 2, d: 1.0 },
  { top: "38%", left: "54%", s: 1.5, d: 0.2 },
  { top: "50%", left: "12%", s: 2, d: 2.5 },
  { top: "66%", left: "80%", s: 1.5, d: 1.7 },
  { top: "84%", left: "68%", s: 2, d: 0.5 },
  { top: "6%", left: "70%", s: 1.5, d: 1.3 },
  { top: "44%", left: "38%", s: 1.5, d: 2.2 },
];

export default function SpaceBackdrop() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const starColor = isDark
    ? theme.palette.common.white
    : theme.palette.grey[500];
  const starGlow = isDark ? "0 0 6px 1px rgba(255,255,255,0.5)" : "none";
  // Brand violet, not primary.main — dark mode's primary is monochrome white
  // by design, which would drain the rings of colour.
  const ringColor = alpha(theme.palette.purple[500], isDark ? 0.1 : 0.18);
  const trailColor = isDark
    ? theme.palette.common.white
    : theme.palette.purple[500];
  const trailOpacity = isDark ? 0.9 : 0.55;

  return (
    <Box
      aria-hidden
      sx={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        pointerEvents: "none",
        zIndex: 0,
      }}
    >
      {/* Faint orbit rings */}
      {[340, 560].map((size) => (
        <Box
          key={size}
          sx={{
            position: "absolute",
            top: "6%",
            left: "50%",
            width: size,
            height: size,
            transform: "translateX(-50%)",
            borderRadius: "50%",
            border: "1px solid",
            borderColor: ringColor,
          }}
        />
      ))}

      {/* Stars */}
      {STARS.map((star, i) => (
        <Box
          key={i}
          sx={{
            position: "absolute",
            top: star.top,
            left: star.left,
            width: star.s,
            height: star.s,
            borderRadius: "50%",
            bgcolor: starColor,
            boxShadow: starGlow,
            animation: `${twinkle} ${2.4 + (i % 5) * 0.6}s ease-in-out ${star.d}s infinite`,
          }}
        />
      ))}

      {/* Shooting stars */}
      {[
        { top: "12%", left: "8%", delay: 2 },
        { top: "40%", left: "30%", delay: 7 },
      ].map((sh, i) => (
        <Box
          key={`shoot-${i}`}
          sx={{
            position: "absolute",
            top: sh.top,
            left: sh.left,
            width: 90,
            height: 1.5,
            borderRadius: 2,
            background: `linear-gradient(90deg, ${alpha(trailColor, trailOpacity)}, ${alpha(trailColor, 0)})`,
            filter: `drop-shadow(0 0 6px ${alpha(trailColor, 0.6)})`,
            opacity: 0,
            animation: `${shoot} 9s ease-in ${sh.delay}s infinite`,
          }}
        />
      ))}
    </Box>
  );
}
