// A Level 2 catalog tile (lane Ra MUST 2): 72 px, logo 28, name, language
// chips and badge (no status tag: every way to run works, options-truth L1
// C11); the tooltip names the package or the harness command and the graph
// quality (D12, honest).
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { BADGES, OTEL_PREVIEW_REASON } from "./catalog";
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
}) {
  if (!entry) return null;
  const badge = entry.badge ? BADGES[entry.badge] : null;
  return (
    <CustomTooltip
      show
      title={
        <span style={{ whiteSpace: "pre-line" }}>
          {tileTooltip(entry, environment)}
        </span>
      }
      size="small"
      placement="top"
      arrow
      enterDelay={500}
    >
      <Box
        role="button"
        tabIndex={tabIndex}
        data-testid={`catalog-tile-${entry.id}`}
        data-tile="1"
        data-first={first ? "1" : undefined}
        onClick={onPick}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onPick?.();
            return;
          }
          onKeyDown?.(e);
        }}
        sx={{
          height: 72,
          boxSizing: "border-box",
          border: "1px solid",
          borderColor: first ? "purple.500" : "border.light",
          borderRadius: 0.75,
          px: 1,
          py: 0.75,
          display: "flex",
          alignItems: "center",
          gap: 1,
          cursor: "pointer",
          bgcolor: "background.paper",
          outline: "none",
          minWidth: 0,
          "&:hover, &:focus-visible": {
            borderColor: "purple.500",
            bgcolor: "purple.o5",
          },
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
  /** Kept for callers; tiles carry no status tag any more. */
  hideStatus: PropTypes.bool,
};
