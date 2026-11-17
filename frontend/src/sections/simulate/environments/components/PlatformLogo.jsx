import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { PLATFORM_LOGOS } from "./platformLogos";

/**
 * A platform's brand mark.
 *
 * Renders the vendor's official logo (inlined, so it inherits `currentColor`
 * and adapts to light/dark). Platforms without a bundled logo — e.g. the chat
 * providers — fall back to a neutral monogram tile.
 *
 * `decorative`: the name is shown as text beside the logo, so the logo is
 * hidden from screen readers and the name is read once. Otherwise the logo
 * is an image named for the platform.
 */
export default function PlatformLogo({ id, name, decorative = false }) {
  const spec = PLATFORM_LOGOS[id];
  if (spec) {
    const isMark = spec.type === "mark";
    return (
      <Box
        role={decorative ? undefined : "img"}
        aria-label={name}
        aria-hidden={decorative ? "true" : undefined}
        dangerouslySetInnerHTML={{ __html: spec.svg }}
        sx={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          ...(spec.keepColor ? {} : { color: spec.color ?? "text.primary" }),
          "& svg": {
            height: isMark ? 16 : 14,
            width: isMark ? 16 : "auto",
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
      aria-hidden={decorative ? "true" : undefined}
      sx={{
        width: 15,
        height: 15,
        borderRadius: 0.5,
        flexShrink: 0,
        bgcolor: "action.selected",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Typography
        sx={{
          fontSize: 11,
          fontWeight: 600,
          color: "text.secondary",
          lineHeight: 1,
        }}
      >
        {(name || "?").trim()[0]?.toUpperCase()}
      </Typography>
    </Box>
  );
}
PlatformLogo.propTypes = {
  id: PropTypes.string,
  name: PropTypes.string,
  decorative: PropTypes.bool,
};
