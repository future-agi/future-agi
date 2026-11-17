import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

/** Opened from a URL, still loading: 3 card skeletons (no overlay). */
export const RowSkeleton = () => (
  <Stack gap={1.5} sx={{ p: 2 }} data-testid="row-view-skeleton">
    {[96, 140, 120].map((h, i) => (
      <Box
        key={i}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          p: 1.25,
        }}
      >
        <Skeleton width={140} height={20} />
        <Skeleton variant="rounded" height={h - 40} sx={{ mt: 1 }} />
      </Box>
    ))}
  </Stack>
);

/**
 * RV#28: `Row not found` with a way out — Close, Go to row 1, and the
 * version history (`Deleted in v5 by Asha Rao` when the row is known).
 * PF-4 (PUX-8): the history door and its sentence show only when
 * `onOpenHistory` is given (a launch build hides dataset.versions).
 */
export const RowNotFound = ({ onOpenHistory, onClose, onFirstRow, detail }) => (
  <Stack
    alignItems="center"
    gap={1.5}
    sx={{ py: 8, px: 3, textAlign: "center" }}
    data-testid="row-not-found"
  >
    <Iconify icon="mdi:table-off" width={30} sx={{ color: "text.disabled" }} />
    <Typography
      typography="s1"
      color="text.primary"
      fontWeight="fontWeightMedium"
    >
      Row not found
    </Typography>
    <Typography typography="s2" color="text.secondary" sx={{ maxWidth: 360 }}>
      {detail ??
        (onOpenHistory
          ? "It may have been deleted, or the link points to another dataset. The version history lists every deleted row."
          : "It may have been deleted, or the link points to another dataset.")}
    </Typography>
    <Stack
      direction="row"
      gap={1}
      sx={{ flexWrap: "wrap", justifyContent: "center" }}
    >
      {onFirstRow && (
        <Button size="small" variant="contained" onClick={onFirstRow}>
          Go to row 1
        </Button>
      )}
      {onOpenHistory && (
        <Button size="small" variant="outlined" onClick={onOpenHistory}>
          View version history
        </Button>
      )}
      {onClose && (
        <Button size="small" color="inherit" onClick={onClose}>
          Close
        </Button>
      )}
    </Stack>
  </Stack>
);

RowNotFound.propTypes = {
  onOpenHistory: PropTypes.func,
  onClose: PropTypes.func,
  onFirstRow: PropTypes.func,
  detail: PropTypes.string,
};

const TONES = {
  amber: (t) => ({
    bgcolor: alpha(t.palette.amber[500], 0.12),
    color:
      t.palette.mode === "dark" ? t.palette.amber[500] : t.palette.amber[800],
    icon: "mdi:alert-outline",
  }),
  grey: (t) => ({
    bgcolor: t.palette.action.hover,
    color: t.palette.text.secondary,
    icon: "mdi:eye-outline",
  }),
  red: (t) => ({
    bgcolor: alpha(t.palette.red[600], 0.1),
    color: t.palette.mode === "dark" ? t.palette.red[400] : t.palette.red[700],
    icon: "solar:trash-bin-trash-linear",
  }),
};

/**
 * integration: the lock banner of a row deleted after the viewed version —
 * `Deleted in v5 · not in the latest version (v6).` (`{version, latest}`);
 * a row restored since (`inLatest`, `restoredIn`) —
 * `Deleted in v5 · restored in v7.`
 */
export const deletedRowText = ({
  version,
  latest,
  inLatest = false,
  restoredIn = null,
} = {}) => {
  const head = version ? `Deleted in v${version}` : "Deleted";
  if (inLatest)
    return `${head} · ${restoredIn ? `restored in v${restoredIn}` : "in the latest version again"}.`;
  return `${head} · not in the latest version${latest ? ` (v${latest})` : ""}.`;
};

/**
 * integration (DM-07): the kept-copy words of an expired source, from the source
 * itself (`retention_days`, `project_name`, trace or session).
 */
const sourceWhat = (source) =>
  source?.kind === "session" || source?.type === "session"
    ? "session"
    : "trace";
const afterDays = (source) =>
  Number.isFinite(source?.retention_days)
    ? ` after ${source.retention_days} ${source.retention_days === 1 ? "day" : "days"}`
    : "";
export const expiredCopyText = (source) =>
  `Showing the copy saved at import (the original ${sourceWhat(source)} expired${afterDays(source)}${
    source?.project_name ? ` in ${source.project_name}` : ""
  }).`;
export const expiredOriginalText = (source) =>
  `The original ${sourceWhat(source)} left ${source?.project_name ?? "Observe"}${afterDays(source)}; this tab shows the copy saved at import.`;

/** A one-line banner at the top of the panel body, with one action. */
export const RowBanner = ({
  tone = "grey",
  children,
  action,
  onAction,
  testId,
  icon,
}) => (
  <Box
    data-testid={testId}
    sx={(t) => {
      const c = TONES[tone](t);
      return {
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 2,
        py: 0.75,
        bgcolor: c.bgcolor,
        color: c.color,
        borderBottom: "1px solid",
        borderColor: "divider",
      };
    }}
  >
    <Box
      component="span"
      sx={(t) => ({ display: "inline-flex", color: TONES[tone](t).color })}
    >
      <Iconify
        icon={
          icon ??
          (tone === "red"
            ? "solar:trash-bin-trash-linear"
            : tone === "amber"
              ? "mdi:alert-outline"
              : "mdi:eye-outline")
        }
        width={16}
      />
    </Box>
    <Typography
      typography="s2"
      sx={{ flex: 1, color: "inherit", fontWeight: 500 }}
    >
      {children}
    </Typography>
    {action && (
      <Button
        size="small"
        color="inherit"
        onClick={onAction}
        sx={{ typography: "s2", py: 0 }}
      >
        {action}
      </Button>
    )}
  </Box>
);

RowBanner.propTypes = {
  tone: PropTypes.oneOf(["amber", "grey", "red"]),
  children: PropTypes.node,
  action: PropTypes.string,
  onAction: PropTypes.func,
  testId: PropTypes.string,
  icon: PropTypes.string,
};
