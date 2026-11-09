import React from "react";
import SvgIcon from "@mui/material/SvgIcon";

// Falcon spaceship, pointing up, geometric, matches the brand. Shared by the
// floating launcher and the launcher docked in the Omega mobile bar.
export default function FalconIcon(props) {
  return (
    <SvgIcon {...props} viewBox="0 0 24 24">
      <g transform="rotate(180, 12, 12)">
        <path
          d="M12 3L8 10L7 17L12 20L17 17L16 10Z"
          fill="currentColor"
          opacity="0.25"
        />
        <path
          d="M12 3L8 10L7 17L12 20L17 17L16 10Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M10.5 9L12 6.5L13.5 9"
          fill="currentColor"
          opacity="0.4"
          stroke="currentColor"
          strokeWidth="1"
          strokeLinejoin="round"
        />
        <path
          d="M8 10L4 8.5V12L7 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M16 10L20 8.5V12L17 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M9.5 18L12 21.5L14.5 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.6"
        />
      </g>
    </SvgIcon>
  );
}
