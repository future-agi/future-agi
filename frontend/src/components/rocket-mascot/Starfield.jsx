import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useTheme } from "@mui/material/styles";

const STARS = [
  { top: "5%", left: "8%", cls: "star star-far" },
  { top: "12%", left: "25%", cls: "star star-far" },
  { top: "8%", left: "45%", cls: "star star-far" },
  { top: "15%", left: "72%", cls: "star star-far" },
  { top: "6%", left: "88%", cls: "star star-far" },
  { top: "10%", left: "15%", cls: "star star-mid" },
  { top: "18%", left: "60%", cls: "star star-mid" },
  { top: "22%", left: "85%", cls: "star star-mid" },
  { top: "8%", left: "35%", cls: "star star-close" },
  { top: "20%", left: "78%", cls: "star star-close" },
  { top: "40%", left: "3%", cls: "star star-far" },
  { top: "55%", left: "5%", cls: "star star-far" },
  { top: "40%", right: "4%", cls: "star star-far" },
  { top: "60%", right: "6%", cls: "star star-far" },
  { top: "35%", left: "8%", cls: "star star-mid" },
  { top: "50%", right: "10%", cls: "star star-mid" },
  { top: "70%", left: "20%", cls: "star star-far" },
  { top: "75%", left: "65%", cls: "star star-far" },
  { top: "80%", left: "40%", cls: "star star-mid" },
  { top: "85%", left: "90%", cls: "star star-close" },
];

const Starfield = ({ density = "full", showGrid = false, sx = {} }) => {
  const theme = useTheme();
  const starColor = theme.palette.common.white;
  const stars = density === "sparse" ? STARS.slice(0, 10) : STARS;
  return (
    <Box
      sx={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        overflow: "hidden",
        "& .star": {
          position: "absolute",
          background: starColor,
          borderRadius: "50%",
        },
        "& .star-far": {
          width: "1px",
          height: "1px",
          opacity: 0.3,
          animation: "fagi-twinkle 4s ease-in-out infinite",
        },
        "& .star-mid": {
          width: 2,
          height: 2,
          opacity: 0.4,
          animation: "fagi-twinkle 3s ease-in-out infinite",
        },
        "& .star-close": {
          width: 3,
          height: 3,
          opacity: 0.6,
          animation: "fagi-twinkle 2.5s ease-in-out infinite",
        },
        ...(showGrid && {
          "&::before": {
            content: '""',
            position: "absolute",
            inset: 0,
            opacity: 0.015,
            backgroundImage:
              `linear-gradient(${starColor} 1px, transparent 1px), linear-gradient(90deg, ${starColor} 1px, transparent 1px)`,
            backgroundSize: "60px 60px",
          },
        }),
        "@keyframes fagi-twinkle": {
          "0%, 100%": { opacity: 0.2, transform: "scale(1)" },
          "50%": { opacity: 1, transform: "scale(1.2)" },
        },
        "@media (prefers-reduced-motion: reduce)": {
          "& .star": { animation: "none" },
        },
        ...sx,
      }}
    >
      {stars.map((s, i) => (
        <div
          key={i}
          className={s.cls}
          style={{
            top: s.top,
            left: s.left,
            right: s.right,
            animationDelay: `${i % 3 === 0 ? 0.5 : i % 3 === 1 ? 1.2 : 0.8}s`,
          }}
        />
      ))}
    </Box>
  );
};

Starfield.propTypes = {
  density: PropTypes.oneOf(["sparse", "full"]),
  showGrid: PropTypes.bool,
  sx: PropTypes.object,
};

export default Starfield;
