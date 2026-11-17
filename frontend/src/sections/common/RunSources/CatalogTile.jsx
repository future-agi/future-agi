// A Level 2 catalog tile (lane Ra MUST 2): 72 px, logo 28, name, language
// chips and badge. Every source the launch shows gets a tile; one the
// server's report (useRunSourceAvailability) does not let this workspace
// run is greyed with the reason under its name and in its tooltip, stays
// keyboard-reachable (aria-disabled) and cannot be picked. A held source
// (Traces I already have, Upload results) is greyed with "Not available in
// this release" the same way. Every greyed tile stays in the tab order so
// its reason can be read, is aria-disabled, and is never picked. A greyed
// tile is named by its source and described by its reason.
import React, { useId } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { BADGES, OTEL_PREVIEW_REASON, tileUnavailableReason } from "./catalog";
import { commandPreview, defaultRecipe } from "./recipe/recipeModel";
import SourceLogo from "./SourceLogo";

/**
 * The command a harness tile shows (C14): the default recipe's command
 * preview on this dataset's Environment (its MCP server, its time limit),
 * so the tile, Level 3 and the generated script print the same line.
 */
export function harnessCommandOf(entry, environment = null) {
  if (entry?.kind !== "harness") return null;
  try {
    return commandPreview(defaultRecipe(entry, environment)).text || null;
  } catch {
    return null;
  }
}

const LANG = { python: "Py", typescript: "TS" };

const GRAPH_LINE = {
  full: () => "Graph shows every step (you set graph.node.*)",
  parent_child: () => "Graph shows parent → child only",
  model_calls: () => "Graph shows model calls only",
  unknown: () => "Graph: not known until you check one row's spans",
};

/** The tile tooltip: package · instrumentor, then graph quality. */
export function tileTooltip(entry, environment = null) {
  const ins = entry?.instrument;
  const pkg = ins?.pip ?? ins?.npm ?? null;
  const cls =
    ins?.import?.match(/import \{?\s*([\w, ]+?)\s*\}?;?$/)?.[1] ?? null;
  const lines = [];
  if (pkg) lines.push(cls ? `${pkg} · ${cls}` : pkg);
  else if (entry?.badge === "otel_preview") lines.push(OTEL_PREVIEW_REASON);
  const cmd = harnessCommandOf(entry, environment);
  if (cmd) lines.push(cmd);
  if (entry?.graph?.quality)
    lines.push(GRAPH_LINE[entry.graph.quality]?.() ?? entry.graph.source);
  return lines.join("\n");
}

export default function CatalogTile({
  entry,
  onPick,
  first,
  extra,
  tabIndex = 0,
  onKeyDown,
  environment = null,
  availability,
}) {
  const reasonId = useId();
  if (!entry) return null;
  const badge = entry.badge ? BADGES[entry.badge] : null;
  const unavailableReason = tileUnavailableReason(entry, availability);
  const disabled = Boolean(unavailableReason);
  return (
    <CustomTooltip
      show
      title={
        // A greyed tile's tooltip is its reason, as text, so its own
        // aria-label (the source's name) names it.
        unavailableReason ?? (
          <span style={{ whiteSpace: "pre-line" }}>
            {tileTooltip(entry, environment)}
          </span>
        )
      }
      size="small"
      placement="top"
      arrow
      enterDelay={500}
    >
      <Box
        role="button"
        tabIndex={tabIndex}
        aria-disabled={disabled || undefined}
        aria-label={disabled ? entry.name : undefined}
        aria-describedby={disabled ? reasonId : undefined}
        data-testid={`catalog-tile-${entry.id}`}
        data-tile="1"
        data-first={first && !disabled ? "1" : undefined}
        onClick={disabled ? undefined : onPick}
        onKeyDown={(e) => {
          if (disabled) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onPick?.();
            return;
          }
          onKeyDown?.(e);
        }}
        sx={{
          height: disabled ? "auto" : 72,
          minHeight: 72,
          boxSizing: "border-box",
          border: "1px solid",
          borderColor: first && !disabled ? "purple.500" : "border.light",
          borderRadius: 0.75,
          px: 1,
          py: 0.75,
          display: "flex",
          alignItems: "center",
          gap: 1,
          cursor: disabled ? "not-allowed" : "pointer",
          bgcolor: disabled ? "background.neutral" : "background.paper",
          outline: "none",
          minWidth: 0,
          ...(!disabled && {
            "&:hover, &:focus-visible": {
              borderColor: "purple.500",
              bgcolor: "purple.o5",
            },
          }),
          "&:focus-visible": {
            boxShadow: (t) => `0 0 0 2px ${t.palette.primary.main}`,
          },
        }}
      >
        <SourceLogo entry={entry} size={28} />
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
            {entry.name}
          </Typography>
          {unavailableReason ? (
            <Typography id={reasonId} typography="s3" color="text.secondary">
              {unavailableReason}
            </Typography>
          ) : null}
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ minWidth: 0, overflow: "hidden" }}
          >
            {(entry.languages ?? []).map((l) => (
              <Typography
                key={l}
                component="span"
                typography="s3"
                sx={{
                  px: 0.5,
                  borderRadius: 0.5,
                  bgcolor: "background.neutral",
                  color: "text.secondary",
                  flexShrink: 0,
                }}
              >
                {LANG[l] ?? l}
              </Typography>
            ))}
            {badge ? (
              <Typography
                component="span"
                typography="s3"
                color="text.secondary"
                noWrap
              >
                {badge}
              </Typography>
            ) : null}
            {extra}
          </Stack>
        </Box>
      </Box>
    </CustomTooltip>
  );
}

CatalogTile.propTypes = {
  entry: PropTypes.object,
  onPick: PropTypes.func,
  first: PropTypes.bool,
  extra: PropTypes.node,
  tabIndex: PropTypes.number,
  onKeyDown: PropTypes.func,
  /** The dataset's Environment: a harness tile prints Level 3's command. */
  environment: PropTypes.object,
  /** The server's run-source report (useRunSourceAvailability). */
  availability: PropTypes.object,
  /** Kept for callers; tiles carry no status tag any more. */
  hideStatus: PropTypes.bool,
};
