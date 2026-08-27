import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";
import SvgColor from "../svg-color";
import { Starfield } from "../rocket-mascot";

// ---------------------------------------------------------------------------
// HUD-style futuristic splash screen (no gradients).
//
// Visual language uses only solid strokes, fills, and borders:
//   * Corner brackets that frame up around the central logo
//   * Rotating crosshair ring with tick marks
//   * Counter-rotating dashed outer orbit
//   * Segmented progress bar with 16 cells
//   * Terminal-style boot log ticking through stages
//   * Optional starfield in dark mode
// ---------------------------------------------------------------------------

const BOOT_LOG = [
  "Establishing secure uplink...",
  "Authenticating mission profile...",
  "Syncing workspaces...",
  "Calibrating evaluators...",
  "Warming up the engines...",
  "Systems online.",
];

export default function SplashScreen({ sx, ...other }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const primary = theme.palette.primary.main;
  const line = isDark
    ? alpha(theme.palette.common.white, 0.55)
    : theme.palette.text.primary;
  const faintLine = isDark
    ? alpha(theme.palette.common.white, 0.18)
    : alpha(theme.palette.text.primary, 0.2);

  const [mounted, setMounted] = useState(false);
  const [logIndex, setLogIndex] = useState(0);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return undefined;
    const id = setInterval(() => {
      setLogIndex((i) => (i + 1) % BOOT_LOG.length);
    }, 900);
    return () => clearInterval(id);
  }, [mounted]);

  // Deterministic particle positions so they don't flicker between renders
  const particles = useMemo(
    () =>
      Array.from({ length: 5 }).map((_, i) => ({
        top: `${12 + ((i * 19) % 76)}%`,
        left: `${(i * 29) % 100}%`,
        delay: `${i * 0.5}s`,
        dur: `${5 + (i % 3)}s`,
      })),
    [],
  );

  if (!mounted) return null;

  return (
    <Box
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
        gap: 4,
        overflow: "hidden",
        bgcolor: "background.default",
        "@media (prefers-reduced-motion: reduce)": {
          "& *, & *::before, & *::after": {
            animationDuration: "0.01ms !important",
            animationIterationCount: "1 !important",
            transitionDuration: "0.01ms !important",
          },
        },
        ...sx,
      }}
      {...other}
    >
      {/* Starfield only in dark mode */}
      {isDark && <Starfield density="full" />}

      {/* Subtle grid overlay — pure repeating lines, no gradient */}
      <Box
        sx={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          opacity: isDark ? 0.04 : 0.03,
          backgroundImage: `linear-gradient(${line} 1px, transparent 1px), linear-gradient(90deg, ${line} 1px, transparent 1px)`,
          backgroundSize: "48px 48px",
        }}
      />

      {/* Floating accent dots */}
      {particles.map((p, i) => (
        <Box
          key={i}
          sx={{
            position: "absolute",
            top: p.top,
            left: p.left,
            width: 2,
            height: 2,
            bgcolor: primary,
            opacity: 0.5,
            animation: `splash-float ${p.dur} ease-in-out infinite`,
            animationDelay: p.delay,
            pointerEvents: "none",
            "@keyframes splash-float": {
              "0%, 100%": { transform: "translateY(0)", opacity: 0.25 },
              "50%": { transform: "translateY(-16px)", opacity: 0.8 },
            },
          }}
        />
      ))}

      {/* ============ HUD rig framing the logo ============ */}
      <Box
        sx={{
          position: "relative",
          width: 240,
          height: 240,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {/* Corner brackets that "lock on" to the center */}
        {[
          { top: 0, left: 0, rot: 0 },
          { top: 0, right: 0, rot: 90 },
          { bottom: 0, right: 0, rot: 180 },
          { bottom: 0, left: 0, rot: 270 },
        ].map((c, i) => (
          <Box
            key={i}
            sx={{
              position: "absolute",
              top: c.top,
              left: c.left,
              right: c.right,
              bottom: c.bottom,
              width: 28,
              height: 28,
              transform: `rotate(${c.rot}deg)`,
              "&::before": {
                content: '""',
                position: "absolute",
                top: 0,
                left: 0,
                width: 28,
                height: 2,
                bgcolor: primary,
              },
              "&::after": {
                content: '""',
                position: "absolute",
                top: 0,
                left: 0,
                width: 2,
                height: 28,
                bgcolor: primary,
              },
              animation: "splash-bracket 2.4s ease-out infinite",
              animationDelay: `${i * 0.12}s`,
              "@keyframes splash-bracket": {
                "0%": { opacity: 0, transform: `rotate(${c.rot}deg) translate(12px, 12px)` },
                "40%, 100%": { opacity: 1, transform: `rotate(${c.rot}deg) translate(0, 0)` },
              },
            }}
          />
        ))}

        {/* Crosshair SVG — rotating rings with tick marks */}
        <svg
          width="240"
          height="240"
          viewBox="0 0 240 240"
          style={{ position: "absolute", inset: 0 }}
          fill="none"
        >
          {/* Horizontal + vertical crosshair lines */}
          <line
            x1="40"
            y1="120"
            x2="88"
            y2="120"
            stroke={faintLine}
            strokeWidth="1"
          />
          <line
            x1="152"
            y1="120"
            x2="200"
            y2="120"
            stroke={faintLine}
            strokeWidth="1"
          />
          <line
            x1="120"
            y1="40"
            x2="120"
            y2="88"
            stroke={faintLine}
            strokeWidth="1"
          />
          <line
            x1="120"
            y1="152"
            x2="120"
            y2="200"
            stroke={faintLine}
            strokeWidth="1"
          />

          {/* Inner static ring */}
          <circle
            cx="120"
            cy="120"
            r="68"
            stroke={faintLine}
            strokeWidth="1"
          />

          {/* Rotating dashed middle ring */}
          <g>
            <circle
              cx="120"
              cy="120"
              r="88"
              stroke={primary}
              strokeWidth="1.5"
              strokeDasharray="8 12"
            />
            <animateTransform
              attributeName="transform"
              type="rotate"
              from="0 120 120"
              to="360 120 120"
              dur="5s"
              repeatCount="indefinite"
            />
          </g>

          {/* Counter-rotating outer ring with tick marks */}
          <g>
            <circle
              cx="120"
              cy="120"
              r="112"
              stroke={faintLine}
              strokeWidth="1"
              strokeDasharray="1 7"
            />
            <animateTransform
              attributeName="transform"
              type="rotate"
              from="360 120 120"
              to="0 120 120"
              dur="14s"
              repeatCount="indefinite"
            />
          </g>

          {/* Cardinal tick marks */}
          {[0, 90, 180, 270].map((deg) => (
            <line
              key={deg}
              x1="120"
              y1="4"
              x2="120"
              y2="14"
              stroke={line}
              strokeWidth="1.5"
              transform={`rotate(${deg} 120 120)`}
            />
          ))}

          {/* Intercardinal smaller ticks */}
          {[45, 135, 225, 315].map((deg) => (
            <line
              key={deg}
              x1="120"
              y1="4"
              x2="120"
              y2="10"
              stroke={faintLine}
              strokeWidth="1"
              transform={`rotate(${deg} 120 120)`}
            />
          ))}

          {/* Satellite on the middle ring */}
          <g>
            <rect
              x="206"
              y="117"
              width="6"
              height="6"
              fill={primary}
              stroke="none"
            >
              <animate
                attributeName="opacity"
                values="0.6;1;0.6"
                dur="1s"
                repeatCount="indefinite"
              />
            </rect>
            <animateTransform
              attributeName="transform"
              type="rotate"
              from="0 120 120"
              to="360 120 120"
              dur="4s"
              repeatCount="indefinite"
            />
          </g>
        </svg>

        {/* Logo block at center — solid border, no gradient fill */}
        <Box
          sx={{
            position: "relative",
            zIndex: 2,
            width: 64,
            height: 64,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "background.paper",
            border: `2px solid ${primary}`,
            borderRadius: "4px",
            // Tiny corner notches cut into the square for an HUD feel
            "&::before, &::after": {
              content: '""',
              position: "absolute",
              width: 8,
              height: 8,
              border: `2px solid ${primary}`,
              bgcolor: "background.paper",
            },
            "&::before": {
              top: -5,
              left: -5,
              borderRight: "none",
              borderBottom: "none",
            },
            "&::after": {
              bottom: -5,
              right: -5,
              borderLeft: "none",
              borderTop: "none",
            },
            animation: "splash-logo-pulse 2.2s ease-in-out infinite",
            "@keyframes splash-logo-pulse": {
              "0%, 100%": { transform: "scale(1)" },
              "50%": { transform: "scale(1.04)" },
            },
          }}
        >
          <SvgColor
            src="/favicon/logo.svg"
            sx={{
              width: 32,
              height: 32,
              color: isDark ? theme.palette.common.white : primary,
            }}
          />
        </Box>
      </Box>

      {/* ============ Wordmark ============ */}
      <Box sx={{ textAlign: "center", zIndex: 2 }}>
        <Typography
          variant="l3"
          fontWeight="fontWeightBold"
          letterSpacing="0.22em"
          color="text.primary"
          sx={{ textTransform: "uppercase", fontSize: "22px" }}
        >
          Future&nbsp;AGI
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.25,
            mt: 0.75,
          }}
        >
          <Box
            sx={{
              width: 24,
              height: "1px",
              bgcolor: faintLine,
            }}
          />
          <Typography
            fontFamily="IBM Plex Mono, ui-monospace, monospace"
            fontSize="10.5px"
            color="text.secondary"
            letterSpacing="0.38em"
            sx={{ textTransform: "uppercase" }}
          >
            Mission&nbsp;Control
          </Typography>
          <Box
            sx={{
              width: 24,
              height: "1px",
              bgcolor: faintLine,
            }}
          />
        </Box>
      </Box>

      {/* ============ Terminal boot log ============ */}
      <Box
        sx={{
          zIndex: 2,
          fontFamily: "IBM Plex Mono, ui-monospace, monospace",
          fontSize: "11.5px",
          color: "text.secondary",
          display: "flex",
          alignItems: "center",
          gap: 1,
          minHeight: 18,
          px: 2,
          py: 0.75,
          border: `1px solid ${faintLine}`,
          borderRadius: "2px",
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.02)
            : alpha(theme.palette.common.black, 0.02),
          minWidth: 320,
          justifyContent: "center",
        }}
      >
        <Box
          component="span"
          sx={{
            width: 6,
            height: 6,
            bgcolor: primary,
            animation: "splash-blink 1s steps(2, end) infinite",
            "@keyframes splash-blink": {
              "0%, 50%": { opacity: 1 },
              "51%, 100%": { opacity: 0.25 },
            },
          }}
        />
        <Box component="span" sx={{ color: "text.disabled" }}>
          &gt;
        </Box>
        <Box
          component="span"
          key={logIndex}
          sx={{
            animation: "splash-type 0.35s ease-out",
            "@keyframes splash-type": {
              from: { opacity: 0, transform: "translateX(-4px)" },
              to: { opacity: 1, transform: "translateX(0)" },
            },
          }}
        >
          {BOOT_LOG[logIndex]}
        </Box>
      </Box>

      {/* ============ Segmented progress strip ============ */}
      <Box
        sx={{
          zIndex: 2,
          display: "flex",
          gap: "3px",
          mt: -1.5,
        }}
      >
        {Array.from({ length: 16 }).map((_, i) => (
          <Box
            key={i}
            sx={{
              width: 14,
              height: 6,
              bgcolor: faintLine,
              border: `1px solid ${faintLine}`,
              animation: "splash-cell 1.8s ease-in-out infinite",
              animationDelay: `${i * 0.06}s`,
              "@keyframes splash-cell": {
                "0%, 70%, 100%": {
                  background: "transparent",
                  borderColor: faintLine,
                },
                "35%": {
                  background: primary,
                  borderColor: primary,
                },
              },
            }}
          />
        ))}
      </Box>
    </Box>
  );
}

SplashScreen.propTypes = {
  sx: PropTypes.object,
};
