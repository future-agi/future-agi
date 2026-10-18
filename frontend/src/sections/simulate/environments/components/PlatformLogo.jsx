import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { PLATFORM_LOGOS } from "./platformLogos";

/**
 * A platform's brand mark.
 *
 * Renders the vendor's official logo (inlined, so it inherits `currentColor`
 * and adapts to light/dark). Platforms without a bundled logo — e.g. the chat
 * providers — fall back to a brand-coloured monogram tile so the picker still
 * reads as branded.
 */
export default function PlatformLogo({ id, name, brand }) {
  const spec = PLATFORM_LOGOS[id];
  if (spec) {
    const isMark = spec.type === "mark";
    return (
      <Box
        aria-label={name}
        dangerouslySetInnerHTML={{ __html: spec.svg }}
        sx={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          ...(spec.keepColor ? {} : { color: "text.primary" }),
          "& svg": {
            height: isMark ? 15 : 14,
            width: isMark ? 15 : "auto",
            maxWidth: spec.maxWidth || 60,
            display: "block",
            ...(spec.keepColor ? {} : { fill: "currentColor" }),
          },
        }}
      />
    );
  }
  return (
    <Box
      sx={{
        width: 15, height: 15, borderRadius: 0.5, flexShrink: 0,
        bgcolor: brand || "text.disabled",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <Typography sx={{ fontSize: 9, fontWeight: "800", color: "#fff", lineHeight: 1 }}>
        {(name || "?").trim()[0]?.toUpperCase()}
      </Typography>
    </Box>
  );
}
PlatformLogo.propTypes = { id: PropTypes.string, name: PropTypes.string, brand: PropTypes.string };
