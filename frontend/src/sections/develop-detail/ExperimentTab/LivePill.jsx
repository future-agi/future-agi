import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import {
  fmtTimeAbsolute,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";
import { pausePolling } from "src/sections/common/Runs/runStatus";

const MENU_SELECTOR =
  ".MuiPopover-root, .MuiMenu-root, .MuiAutocomplete-popper";

const menuOnScreen = () =>
  [...document.querySelectorAll(MENU_SELECTOR)].some((el) => {
    if (el.classList.contains("MuiModal-hidden")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });

/** True while a menu / popover is on screen (rAF-throttled observer). */
export function useMenuOpen() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return undefined;
    let frame = 0;
    const check = () => {
      frame = 0;
      setOpen(menuOnScreen());
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(check);
    };
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true });
    schedule();
    return () => {
      mo.disconnect();
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return open;
}

/** Re-render every `ms` so a relative time stays true. */
function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

/**
 * EL#2 `Live · updated 4s ago` beside the run count: green pulsing dot while
 * a run is moving (polling), `Paused` (and polling actually paused) while a
 * menu, popover or selection is open, `Updated 4m ago` when nothing moves.
 * `variant="inline"` (A2-03, the grid's status footer): no pill, lower
 * case, the dot only while something is running (`updated 1s ago`).
 */
export default function LivePill({
  updatedAt,
  polling,
  paused,
  onResume,
  variant = "pill",
}) {
  const now = useNow(1000);
  useEffect(() => {
    if (!paused || !polling) return undefined;
    const release = pausePolling();
    return () => {
      release();
      onResume?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, polling]);
  if (!updatedAt) return null;
  const iso = new Date(updatedAt).toISOString();
  const secs = Math.max(0, Math.round((now - updatedAt) / 1000));
  // IT-R5 G2 P5: `Updated just now`, not `updated 2s ago`.
  const ago =
    secs < 10
      ? "just now"
      : secs < 60
        ? `${secs}s ago`
        : fmtTimeRelative(iso, { now });
  const live = polling && !paused;
  const inline = variant === "inline";
  const label = polling
    ? paused
      ? inline
        ? "updates paused"
        : "Paused"
      : `${inline ? "live" : "Live"} · updated ${ago}`
    : `Updated ${ago}`;
  const tip = polling
    ? paused
      ? "Updates pause while a menu or a selection is open, so rows don't move under you."
      : `A run is still going: numbers refresh every few seconds. Last update ${fmtTimeAbsolute(iso)}.`
    : `Nothing is running. Last update ${fmtTimeAbsolute(iso)}.`;
  return (
    <CustomTooltip show title={tip} size="small" placement="bottom" arrow>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        data-testid="live-pill"
        aria-live="off"
        sx={
          inline
            ? { flexShrink: 0 }
            : {
                px: 0.75,
                height: 22,
                borderRadius: "11px",
                border: "1px solid",
                borderColor: "divider",
                flexShrink: 0,
              }
        }
      >
        <Box
          component="span"
          sx={{
            display: inline && !polling ? "none" : "inline-block",
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: live
              ? "green.500"
              : paused && polling
                ? "amber.500"
                : "text.disabled",
            "@keyframes faPulse": {
              "0%": { opacity: 1 },
              "50%": { opacity: 0.35 },
              "100%": { opacity: 1 },
            },
            animation: live ? "faPulse 1.6s ease-in-out infinite" : "none",
            "@media (prefers-reduced-motion: reduce)": { animation: "none" },
          }}
        />
        <Typography
          component="span"
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {label}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

LivePill.propTypes = {
  updatedAt: PropTypes.number,
  polling: PropTypes.bool,
  paused: PropTypes.bool,
  onResume: PropTypes.func,
  variant: PropTypes.oneOf(["pill", "inline"]),
};
