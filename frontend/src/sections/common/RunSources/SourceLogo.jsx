// A run source's logo (frozen props). The brand's own mark, bundled
// (brandLogos.js): inline SVG, so a one-colour mark follows the theme
// (currentColor) and a multi-colour mark keeps its colours; nothing is
// fetched at runtime. A monogram tile only when no mark ships for the entry
// (every catalog entry has one; a hand-made entry may not).
import React, { useId, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { catalogEntry } from "./catalog";
import { brandLogoOf, withUniqueIds } from "./brandLogos";

function Monogram({ text, color, size }) {
  const dark = !color;
  return (
    <Box
      aria-hidden
      data-logo-kind="monogram"
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: size >= 24 ? 1 : 0.5,
        bgcolor: dark ? "text.primary" : color,
        color: dark ? "background.paper" : "common.white",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Typography
        component="span"
        sx={{
          fontSize: Math.max(7, Math.round(size * 0.42)),
          fontWeight: 700,
          lineHeight: 1,
          letterSpacing: 0,
          color: "inherit",
        }}
      >
        {text}
      </Typography>
    </Box>
  );
}
Monogram.propTypes = {
  text: PropTypes.string,
  color: PropTypes.string,
  size: PropTypes.number,
};

// A dark tile (LangGraph / LangChain navy, LlamaIndex / Strands / Aider
// black, AutoGen graphite) gets a 1 px light edge on a dark background, so
// the tile reads as a tile; drop-shadow follows the tile's own shape
// (rounded or square) where a box ring would not.
const DARK_EDGE = "drop-shadow(0 0 1px rgba(255,255,255,0.7))";

/** The bundled mark in a `size` box (a mark fills it the same way across brands). */
export function BrandMark({ logo, color, size }) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const html = useMemo(
    () => (logo.svg && logo.hasIds ? withUniqueIds(logo.svg, uid) : logo.svg),
    [logo, uid],
  );
  const inner = `${Math.round((logo.scale ?? 1) * 1000) / 10}%`;
  const box = {
    width: size,
    height: size,
    flexShrink: 0,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  };
  if (logo.mode === "png")
    return (
      <Box aria-hidden data-logo-kind="png" sx={box}>
        <Box
          component="img"
          src={logo.src}
          alt=""
          draggable={false}
          sx={{ width: inner, height: inner, objectFit: "contain" }}
        />
      </Box>
    );
  return (
    <Box
      aria-hidden
      data-logo-kind="svg"
      data-logo-mode={logo.mode}
      dangerouslySetInnerHTML={{ __html: html }}
      sx={{
        ...box,
        color: logo.mode === "mono" ? color ?? "text.primary" : undefined,
        "& svg": { width: inner, height: inner, display: "block" },
        ...(logo.darkEdge
          ? {
              filter: (t) => (t.palette.mode === "dark" ? DARK_EDGE : "none"),
            }
          : null),
      }}
    />
  );
}
BrandMark.propTypes = {
  logo: PropTypes.object.isRequired,
  color: PropTypes.string,
  size: PropTypes.number,
};

/**
 * @param {{entryId?: string, entry?: object, size?: number, withName?:
 *   boolean, tooltip?: string|boolean}} props  `tooltip` true = the name
 */
export default function SourceLogo({
  entryId,
  entry: entryProp,
  size = 20,
  withName = false,
  tooltip,
}) {
  const entry = entryProp ?? catalogEntry(entryId);
  const logo = entry?.logo ?? {};
  const bundled = brandLogoOf(logo.mark ?? entry?.id ?? entryId);
  const name = entry?.name ?? entryId ?? "";
  // The brand the mark names (a saved worker's tile shows its own name).
  const brand = catalogEntry(entry?.id)?.name ?? name;
  const mark = bundled ? (
    <BrandMark logo={bundled} color={logo.color ?? undefined} size={size} />
  ) : (
    <Monogram
      text={logo.monogram ?? name.slice(0, 2).toUpperCase()}
      color={logo.color}
      size={size}
    />
  );
  const body = withName ? (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{ minWidth: 0 }}
      data-source-logo={entry?.id}
    >
      {mark}
      <Typography component="span" typography="s2" color="text.primary" noWrap>
        {name}
      </Typography>
    </Stack>
  ) : (
    <Box
      component="span"
      role="img"
      aria-label={brand}
      sx={{ display: "inline-flex", flexShrink: 0 }}
      data-source-logo={entry?.id}
    >
      {mark}
    </Box>
  );
  const title = tooltip === true ? name : tooltip;
  if (!title) return body;
  return (
    <CustomTooltip show title={title} placement="top" size="small" arrow>
      <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
        {body}
      </Box>
    </CustomTooltip>
  );
}

SourceLogo.propTypes = {
  entryId: PropTypes.string,
  entry: PropTypes.object,
  size: PropTypes.number,
  withName: PropTypes.bool,
  tooltip: PropTypes.oneOfType([PropTypes.string, PropTypes.bool]),
};
