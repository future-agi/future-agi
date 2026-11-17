import React, { useSyncExternalStore } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import {
  fmtTimeAbsolute,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";

// One shared 60 s ticker for every RelativeTime on the page (paused while
// the tab is hidden).
let now = Date.now();
const listeners = new Set();
let timer = null;
const tick = () => {
  if (typeof document !== "undefined" && document.hidden) return;
  now = Date.now();
  listeners.forEach((l) => l());
};
const subscribe = (l) => {
  listeners.add(l);
  if (!timer && typeof window !== "undefined") {
    timer = window.setInterval(tick, 60_000);
    document.addEventListener("visibilitychange", tick);
  }
  return () => {
    listeners.delete(l);
    if (!listeners.size && timer) {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
      timer = null;
    }
  };
};
const getNow = () => now;

/**
 * RelativeTime — `2h ago` with the absolute time on hover
 * (`Oct 12, 2026, 4:08 PM IST (UTC+5:30) · by Ravi Menon`); re-renders
 * every 60 s while visible.
 *
 * Props (frozen):
 * @param {string|number|Date} props.iso
 * @param {string} [props.by]  who did it
 * @param {object} [props.sx]
 */
export default function RelativeTime({ iso, by, sx }) {
  const current = useSyncExternalStore(subscribe, getNow, getNow);
  const rel = fmtTimeRelative(iso, { now: Math.max(current, Date.now() - 1) });
  const abs = fmtTimeAbsolute(iso, { by });
  return (
    <CustomTooltip show size="small" placement="top" title={abs}>
      <Box
        component="time"
        dateTime={typeof iso === "string" ? iso : undefined}
        sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums", ...sx }}
      >
        {rel}
      </Box>
    </CustomTooltip>
  );
}

RelativeTime.propTypes = {
  iso: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.number,
    PropTypes.instanceOf(Date),
  ]),
  by: PropTypes.string,
  sx: PropTypes.object,
};
