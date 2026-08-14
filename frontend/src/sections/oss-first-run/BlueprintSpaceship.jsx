import React from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import { keyframes } from "@mui/system";

// Blueprint-style spaceship: a sleek winged starship framed by the signature
// dashed "almond" arcs (matching the Future AGI marketing illustrations),
// rendered as technical line-art. Inline SVG so it stays crisp.
//
// One ink colour drives the whole drawing — the per-element strokeOpacity below
// then separates hull from arcs in either theme.
const useLineColor = () => {
  const theme = useTheme();
  return theme.palette.mode === "dark" ? "#EAEFF6" : theme.palette.grey[700];
};

const float = keyframes`
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-7px); }
`;

const flow = keyframes`
  to { stroke-dashoffset: -30; }
`;

const pulse = keyframes`
  0%, 100% { opacity: 0.2; }
  50%      { opacity: 0.42; }
`;

export default function BlueprintSpaceship({ size = 194, sx }) {
  const LINE = useLineColor();

  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        animation: `${float} 6.5s ease-in-out infinite`,
        "& .flow": { animation: `${flow} 2.4s linear infinite` },
        "& .arc": { animation: `${pulse} 5s ease-in-out infinite` },
        ...sx,
      }}
    >
      <svg
        viewBox="0 0 200 220"
        fill="none"
        stroke={LINE}
        strokeLinecap="round"
        strokeLinejoin="round"
        xmlns="http://www.w3.org/2000/svg"
        style={{
          width: "100%",
          height: "auto",
          display: "block",
          overflow: "visible",
        }}
      >
        {/* Almond framing arcs */}
        <g className="arc" strokeWidth="1.2" strokeDasharray="5 7">
          <path d="M100 16 C 46 66, 46 154, 100 204" />
          <path d="M100 16 C 154 66, 154 154, 100 204" />
        </g>
        {/* Apex chevrons */}
        <g strokeWidth="1.2" strokeOpacity="0.5">
          <path d="M93 26 L100 16 L107 26" />
          <path d="M93 194 L100 204 L107 194" />
        </g>

        {/* Faint centerline */}
        <line
          className="flow"
          x1="100"
          y1="10"
          x2="100"
          y2="210"
          strokeWidth="1"
          strokeOpacity="0.14"
          strokeDasharray="3 6"
        />

        {/* Wings (swept delta) */}
        <path d="M90 100 L 42 150 L 66 150 L 92 122 Z" strokeWidth="1.5" />
        <path d="M110 100 L 158 150 L 134 150 L 108 122 Z" strokeWidth="1.5" />
        <path d="M90 106 L 64 138" strokeWidth="1" strokeOpacity="0.4" />
        <path d="M110 106 L 136 138" strokeWidth="1" strokeOpacity="0.4" />
        {/* Wing-tip direction ticks */}
        <g strokeWidth="1.1" strokeOpacity="0.45">
          <path d="M42 150 L 36 145 M42 150 L 40 143" />
          <path d="M158 150 L 164 145 M158 150 L 160 143" />
        </g>

        {/* Fuselage */}
        <path
          d="M100 38
             C 91 52, 87 78, 88 104
             C 88 120, 90 134, 94 146
             L 106 146
             C 110 134, 112 120, 112 104
             C 113 78, 109 52, 100 38 Z"
          strokeWidth="1.85"
        />
        {/* Nose cone + hull panel */}
        <path d="M92 64 Q 100 55 108 64" strokeWidth="1.3" />
        <path d="M89 110 L 111 110" strokeWidth="1" strokeOpacity="0.35" />

        {/* Cockpit window */}
        <ellipse cx="100" cy="86" rx="7" ry="10" strokeWidth="1.5" />
        <circle cx="100" cy="86" r="2.6" strokeWidth="1" strokeOpacity="0.6" />

        {/* Engine nozzle + thrust */}
        <path d="M94 146 L 92 159 L 108 159 L 106 146" strokeWidth="1.5" />
        <path
          className="flow"
          d="M96 159 Q 100 178 104 159"
          strokeWidth="1.3"
          strokeOpacity="0.6"
          strokeDasharray="4 4"
        />

        {/* Vertex nodes */}
        <g fill={LINE} stroke="none">
          <circle cx="100" cy="38" r="2.4" />
          <circle cx="42" cy="150" r="2.2" />
          <circle cx="158" cy="150" r="2.2" />
        </g>
      </svg>
    </Box>
  );
}

BlueprintSpaceship.propTypes = {
  size: PropTypes.number,
  sx: PropTypes.object,
};
