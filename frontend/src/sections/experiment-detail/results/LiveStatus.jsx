import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { etaText } from "./liveModel";

/**
 * iterate r2 G3 (P5/P7): one honest status for a view whose runs are still
 * scoring — `Live · 12 of 30 scored · about 40 s left  [Pause updates]`;
 * paused, the rows and counts hold still: `Paused · 9 new results
 * [Update]` (Update takes them in and stays paused; `Resume` goes live).
 */
export default function LiveStatus({
  live,
  paused,
  newCount,
  onPause,
  onUpdate,
  onResume,
  compact: compactProp = false,
  fixText,
}) {
  // The full sentence only where the toolbar has room for it.
  const roomy = useMediaQuery("(min-width:1680px)");
  if (!live && !paused) return null;
  const compact = compactProp || !roomy;
  const eta = live ? etaText(live.eta) : null;
  // Below 1440 px: `12/30 · about 40 s left` and an icon Pause (the toolbar
  // keeps one row).
  // IT-R4 G3 (P2, P7): a fix run in view reads the dock's own counter
  // (one progress source), never a second rows count.
  const text =
    !paused && fixText
      ? fixText
      : paused
        ? `Paused · ${newCount > 0 ? `${newCount} new ${compact ? "" : newCount === 1 ? "result" : "results"}`.trim() : "no new results"}`
        : compact
          ? `${live.scored}/${live.total}${eta ? ` · ${eta}` : ""}`
          : `Live · ${live.scored} of ${live.total} scored${eta ? ` · ${eta}` : ""}`;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ flexShrink: 0, minWidth: 0 }}
      data-testid="results-live"
      data-paused={paused ? "true" : "false"}
    >
      <Box
        component="span"
        aria-hidden
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flexShrink: 0,
          bgcolor: paused ? "text.disabled" : "green.500",
          ...(!paused && {
            animation: "faLivePulse 1.6s ease-in-out infinite",
            "@keyframes faLivePulse": {
              "0%, 100%": { opacity: 1 },
              "50%": { opacity: 0.35 },
            },
          }),
        }}
      />
      <CustomTooltip
        show
        size="small"
        arrow
        title={
          paused
            ? "Rows, counts and the verdict are frozen so the cursor and your selection stay put. Update takes in the new results."
            : "Counts cover the rows already scored. Pause to triage without rows moving."
        }
      >
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ fontVariantNumeric: "tabular-nums" }}
          data-testid="results-live-text"
        >
          {text}
        </Typography>
      </CustomTooltip>
      {paused ? (
        <>
          {newCount > 0 ? (
            <Button
              size="small"
              variant="outlined"
              onClick={onUpdate}
              data-testid="results-live-update"
              sx={{ height: 26, px: 1, typography: "s2", minWidth: 0 }}
            >
              Update
            </Button>
          ) : null}
          <Button
            size="small"
            color="inherit"
            onClick={onResume}
            sx={{
              height: 26,
              px: 0.75,
              typography: "s2",
              minWidth: 0,
              color: "text.secondary",
            }}
          >
            Resume
          </Button>
        </>
      ) : compact ? (
        <CustomTooltip show size="small" arrow title="Pause updates">
          <IconButton
            size="small"
            aria-label="Pause updates"
            onClick={onPause}
            data-testid="results-live-pause"
            sx={{ p: 0.25 }}
          >
            <Iconify icon="mdi:pause" width={16} />
          </IconButton>
        </CustomTooltip>
      ) : (
        <Button
          size="small"
          variant="text"
          onClick={onPause}
          data-testid="results-live-pause"
          sx={{
            height: 26,
            px: 1,
            typography: "s2",
            minWidth: 0,
            whiteSpace: "nowrap",
          }}
        >
          Pause updates
        </Button>
      )}
    </Stack>
  );
}

LiveStatus.propTypes = {
  /** IT-R4 G3: the fix run's counter (fixCounterText) when it is live. */
  fixText: PropTypes.string,
  live: PropTypes.object,
  paused: PropTypes.bool,
  newCount: PropTypes.number,
  onPause: PropTypes.func.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onResume: PropTypes.func.isRequired,
  compact: PropTypes.bool,
};
