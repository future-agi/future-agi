import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { keyframes } from "@mui/material/styles";

// Starts and ends off the text, so each pass reads as one sweep.
const pan = keyframes`
  from { background-position: 150% 0; }
  to { background-position: -50% 0; }
`;

/**
 * Text with a light band panning across it, for work in progress such as
 * "Reading…". Takes Typography props; plain secondary text under reduced motion.
 */
export default function ShimmerText({ children, sx, ...props }) {
  return (
    <Typography
      {...props}
      sx={{
        typography: "s1",
        color: "text.secondary",
        backgroundImage: (theme) =>
          `linear-gradient(90deg, ${theme.palette.text.secondary} 35%, ${theme.palette.text.primary} 50%, ${theme.palette.text.secondary} 65%)`,
        backgroundSize: "200% 100%",
        backgroundClip: "text",
        WebkitBackgroundClip: "text",
        // `color` stays set, so a noWrap ellipsis still has a colour to draw.
        WebkitTextFillColor: "transparent",
        animation: `${pan} 1.6s linear infinite`,
        "@media (prefers-reduced-motion: reduce)": {
          animation: "none",
          backgroundImage: "none",
          WebkitTextFillColor: "currentcolor",
        },
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}

ShimmerText.propTypes = {
  children: PropTypes.node,
  sx: PropTypes.object,
};
