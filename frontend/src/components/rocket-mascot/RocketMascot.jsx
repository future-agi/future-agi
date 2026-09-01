import React from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";

/**
 * Brand mascot rocket, stroke-only linework matching the 404 / Houston error
 * pages. Three variants:
 *
 *   "searching"  drifts gently, pings signal waves, rotating "?" — use for
 *                empty states (awaiting data).
 *   "launching"  tilts upward, engines boost, progress ring orbits —
 *                use for loading states.
 *   "broken"     tilted harder, flickering sparks, static ring — use for
 *                failed / error states.
 *
 * Colour automatically follows theme.palette.text — pass `strokeColor` to
 * override. Sized via the `size` prop (default 160).
 */
const RocketMascot = ({
  variant = "searching",
  size = 160,
  strokeColor: strokeColorProp,
  sx = {},
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const strokeColor =
    strokeColorProp ||
    (isDark ? theme.palette.text.disabled : theme.palette.text.secondary);
  const accent = theme.palette.primary.main;

  // Variant-specific wrapper animation (the whole SVG moves).
  const wrapperAnimation = {
    searching: {
      animation: "mascot-drift 8s ease-in-out infinite",
      "@keyframes mascot-drift": {
        "0%, 100%": { transform: "translate(0, 0) rotate(0deg)" },
        "25%": { transform: "translate(10px, -5px) rotate(2deg)" },
        "50%": { transform: "translate(5px, 5px) rotate(-1deg)" },
        "75%": { transform: "translate(-5px, -3px) rotate(1deg)" },
      },
    },
    launching: {
      animation: "mascot-boost 2.4s ease-in-out infinite",
      "@keyframes mascot-boost": {
        "0%, 100%": { transform: "translateY(0) rotate(0deg)" },
        "50%": { transform: "translateY(-6px) rotate(0.5deg)" },
      },
    },
    broken: {
      animation: "mascot-wobble 3.5s ease-in-out infinite",
      "@keyframes mascot-wobble": {
        "0%, 100%": { transform: "translate(0, 0) rotate(-18deg)" },
        "30%": { transform: "translate(-3px, 2px) rotate(-22deg)" },
        "60%": { transform: "translate(4px, -1px) rotate(-14deg)" },
      },
    },
  }[variant];

  // Rocket tilt baked into the SVG <g transform> to match the 404 pose.
  const rocketTilt = {
    searching: "rotate(-15, 100, 100)",
    launching: "rotate(0, 100, 100)",
    broken: "rotate(20, 100, 100)",
  }[variant];

  return (
    <Box
      sx={{
        ...wrapperAnimation,
        "@media (prefers-reduced-motion: reduce)": {
          animation: "none !important",
          "& animate, & animateTransform": { display: "none" },
        },
        ...sx,
      }}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 200 200"
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* ===== Rocket body ===== */}
        <g transform={rocketTilt}>
          {/* Main hull */}
          <path
            d="M100 40 L130 80 L130 140 L100 170 L70 140 L70 80 Z"
            strokeWidth="2"
          />
          {/* Cockpit */}
          <ellipse cx="100" cy="70" rx="12" ry="18" />
          <path d="M88 65 Q100 55 112 65" />
          {/* Engine pods */}
          <ellipse cx="80" cy="155" rx="6" ry="10" />
          <ellipse cx="120" cy="155" rx="6" ry="10" />

          {/* Engine flames — longer + coloured when launching, flicker when broken */}
          {variant === "launching" && (
            <>
              <path
                d="M80 165 L80 188"
                strokeWidth="3"
                stroke={accent}
                opacity="0.8"
              >
                <animate
                  attributeName="stroke-dasharray"
                  values="0 28;14 14;0 28"
                  dur="0.6s"
                  repeatCount="indefinite"
                />
              </path>
              <path
                d="M120 165 L120 188"
                strokeWidth="3"
                stroke={accent}
                opacity="0.8"
              >
                <animate
                  attributeName="stroke-dasharray"
                  values="0 28;14 14;0 28"
                  dur="0.6s"
                  repeatCount="indefinite"
                  begin="0.3s"
                />
              </path>
              <path
                d="M100 170 L100 192"
                strokeWidth="2"
                stroke={accent}
                opacity="0.5"
              >
                <animate
                  attributeName="opacity"
                  values="0.3;0.7;0.3"
                  dur="0.4s"
                  repeatCount="indefinite"
                />
              </path>
            </>
          )}
          {variant === "searching" && (
            <>
              <path d="M80 165 L80 175" strokeWidth="3" opacity="0.4">
                <animate
                  attributeName="opacity"
                  values="0.2;0.5;0.2"
                  dur="0.5s"
                  repeatCount="indefinite"
                />
              </path>
              <path d="M120 165 L120 175" strokeWidth="3" opacity="0.4">
                <animate
                  attributeName="opacity"
                  values="0.2;0.5;0.2"
                  dur="0.5s"
                  repeatCount="indefinite"
                  begin="0.25s"
                />
              </path>
            </>
          )}
          {variant === "broken" && (
            <>
              {/* One engine sputters, the other is out */}
              <path d="M80 165 L80 172" strokeWidth="3" opacity="0.5">
                <animate
                  attributeName="opacity"
                  values="0.1;0.6;0.1;0.3;0.1"
                  dur="0.8s"
                  repeatCount="indefinite"
                />
              </path>
              {/* Spark from the broken engine */}
              <circle cx="120" cy="170" r="1.5" fill={strokeColor} opacity="0.7">
                <animate
                  attributeName="cy"
                  values="170;178;170"
                  dur="0.6s"
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0;0.9;0"
                  dur="0.6s"
                  repeatCount="indefinite"
                />
              </circle>
              <circle cx="125" cy="168" r="1" fill={strokeColor} opacity="0.5">
                <animate
                  attributeName="cy"
                  values="168;180;168"
                  dur="0.8s"
                  repeatCount="indefinite"
                  begin="0.2s"
                />
                <animate
                  attributeName="opacity"
                  values="0;0.7;0"
                  dur="0.8s"
                  repeatCount="indefinite"
                  begin="0.2s"
                />
              </circle>
            </>
          )}

          {/* Wings */}
          <path d="M70 85 L40 70 L35 75 L35 100 L40 105 L70 95" />
          <path d="M130 85 L160 70 L165 75 L165 100 L160 105 L130 95" />
          <circle cx="35" cy="75" r="3" fill={strokeColor} />
          <circle cx="165" cy="75" r="3" fill={strokeColor} />

          {/* Hull details */}
          <line x1="85" y1="100" x2="85" y2="130" opacity="0.5" />
          <line x1="115" y1="100" x2="115" y2="130" opacity="0.5" />
          <circle cx="100" cy="115" r="8" strokeDasharray="2 2" />
        </g>

        {/* ===== Surrounding effects ===== */}
        {variant === "searching" && (
          <g>
            {/* Signal waves expanding out */}
            <circle
              cx="100"
              cy="100"
              r="60"
              strokeDasharray="4 4"
              opacity="0.2"
            >
              <animate
                attributeName="r"
                values="48;72;48"
                dur="3s"
                repeatCount="indefinite"
              />
              <animate
                attributeName="opacity"
                values="0.3;0;0.3"
                dur="3s"
                repeatCount="indefinite"
              />
            </circle>
            <circle
              cx="100"
              cy="100"
              r="80"
              strokeDasharray="4 4"
              opacity="0.15"
            >
              <animate
                attributeName="r"
                values="64;96;64"
                dur="3s"
                repeatCount="indefinite"
                begin="1s"
              />
              <animate
                attributeName="opacity"
                values="0.3;0;0.3"
                dur="3s"
                repeatCount="indefinite"
                begin="1s"
              />
            </circle>
            <circle
              cx="100"
              cy="100"
              r="95"
              strokeDasharray="4 4"
              opacity="0.1"
            >
              <animate
                attributeName="r"
                values="76;114;76"
                dur="3s"
                repeatCount="indefinite"
                begin="2s"
              />
              <animate
                attributeName="opacity"
                values="0.3;0;0.3"
                dur="3s"
                repeatCount="indefinite"
                begin="2s"
              />
            </circle>
            {/* Orbiting question mark */}
            <g>
              <text
                x="165"
                y="55"
                fontSize="24"
                fill={strokeColor}
                opacity="0.4"
                fontFamily="monospace"
              >
                ?
              </text>
              <animateTransform
                attributeName="transform"
                type="rotate"
                from="0 100 100"
                to="360 100 100"
                dur="10s"
                repeatCount="indefinite"
              />
            </g>
          </g>
        )}

        {variant === "launching" && (
          <g>
            {/* Rotating progress ring */}
            <circle
              cx="100"
              cy="100"
              r="88"
              stroke={accent}
              strokeDasharray="10 14"
              opacity="0.35"
              strokeWidth="1.5"
            >
              <animateTransform
                attributeName="transform"
                type="rotate"
                from="0 100 100"
                to="360 100 100"
                dur="6s"
                repeatCount="indefinite"
              />
            </circle>
            {/* Speed lines streaming down */}
            <g opacity="0.5">
              {[
                { x: 55, delay: 0 },
                { x: 100, delay: 0.2 },
                { x: 145, delay: 0.4 },
              ].map((l) => (
                <line
                  key={l.x}
                  x1={l.x}
                  y1="195"
                  x2={l.x}
                  y2="205"
                  strokeWidth="1.5"
                  stroke={accent}
                  opacity="0.6"
                >
                  <animate
                    attributeName="y1"
                    values="195;175;195"
                    dur="1.2s"
                    repeatCount="indefinite"
                    begin={`${l.delay}s`}
                  />
                  <animate
                    attributeName="y2"
                    values="205;195;205"
                    dur="1.2s"
                    repeatCount="indefinite"
                    begin={`${l.delay}s`}
                  />
                  <animate
                    attributeName="opacity"
                    values="0;0.8;0"
                    dur="1.2s"
                    repeatCount="indefinite"
                    begin={`${l.delay}s`}
                  />
                </line>
              ))}
            </g>
          </g>
        )}

        {variant === "broken" && (
          <g>
            {/* Static warning ring with a gap */}
            <path
              d="M 40,100 A 60,60 0 1 1 160,100"
              stroke={strokeColor}
              strokeDasharray="3 5"
              opacity="0.25"
              fill="none"
            />
            {/* Exclamation in orbit */}
            <g>
              <text
                x="162"
                y="60"
                fontSize="22"
                fill={strokeColor}
                opacity="0.5"
                fontFamily="monospace"
                fontWeight="bold"
              >
                !
              </text>
              <animateTransform
                attributeName="transform"
                type="rotate"
                from="0 100 100"
                to="360 100 100"
                dur="14s"
                repeatCount="indefinite"
              />
            </g>
            {/* Distant debris */}
            <circle cx="45" cy="45" r="2" fill={strokeColor} opacity="0.4">
              <animate
                attributeName="opacity"
                values="0.2;0.5;0.2"
                dur="2s"
                repeatCount="indefinite"
              />
            </circle>
            <circle cx="155" cy="155" r="1.5" fill={strokeColor} opacity="0.4">
              <animate
                attributeName="opacity"
                values="0.2;0.5;0.2"
                dur="2.5s"
                repeatCount="indefinite"
                begin="0.5s"
              />
            </circle>
          </g>
        )}
      </svg>
    </Box>
  );
};

RocketMascot.propTypes = {
  variant: PropTypes.oneOf(["searching", "launching", "broken"]),
  size: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  strokeColor: PropTypes.string,
  sx: PropTypes.object,
};

export default RocketMascot;
