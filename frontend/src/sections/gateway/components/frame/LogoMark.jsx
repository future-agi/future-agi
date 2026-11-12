import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";

import { isBlackBackgroundLogo } from "src/components/custom-model-dropdown/common";

// A provider's or an app's mark: its logo when there is one and it loads,
// else its initials on a neutral tile. Small marks (under 24 px) take one
// letter. Black logos are inverted in dark mode, as on Future AGI's AI
// Providers page. Decorative: the name always sits next to it.
function initialsOf(name) {
  const words = String(name || "?")
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean);
  const letters =
    words.length > 1
      ? words[0][0] + words[1][0]
      : (words[0] || "?").slice(0, 2);
  return letters.toUpperCase();
}

const tileSx = (size) => ({
  width: size,
  height: size,
  flexShrink: 0,
  borderRadius: size < 24 ? 0.5 : 1,
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "background.neutral",
  color: "text.primary",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  overflow: "hidden",
  typography: size >= 40 ? "s1" : size >= 24 ? "s2" : "s3",
  fontWeight: 600,
  letterSpacing: "0.02em",
  lineHeight: 1,
});

export default function LogoMark({ name, src, size = 16, framed = false }) {
  const theme = useTheme();
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);

  if (src && !failed) {
    const inner = framed ? Math.round(size * 0.6) : size;
    // A plain img: width and height are attributes, so the layout holds
    // before the image loads.
    const img = (
      <img
        src={src}
        alt=""
        width={inner}
        height={inner}
        loading="lazy"
        // A logo from another site learns nothing about the console page.
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        style={{
          display: "block",
          flexShrink: 0,
          objectFit: "contain",
          ...(theme.palette.mode === "dark" &&
            isBlackBackgroundLogo(src) && {
              filter: "invert(1) brightness(2)",
            }),
        }}
      />
    );
    if (!framed)
      return (
        <Box aria-hidden sx={{ display: "flex" }}>
          {img}
        </Box>
      );
    return (
      <Box aria-hidden sx={{ ...tileSx(size), bgcolor: "background.paper" }}>
        {img}
      </Box>
    );
  }

  const initials = initialsOf(name);
  return (
    <Box aria-hidden sx={tileSx(size)}>
      {size < 24 ? initials.slice(0, 1) : initials}
    </Box>
  );
}

LogoMark.propTypes = {
  name: PropTypes.string,
  src: PropTypes.string,
  size: PropTypes.number,
  framed: PropTypes.bool,
};
