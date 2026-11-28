import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const magnitude = (ms) => {
  const tenths = Math.round(Math.abs(ms) / 100);
  const seconds = Math.floor(tenths / 10);
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}.${tenths % 10}`;
};
const before = (ms) => Math.round(ms / 100) < 0;

// 3000 → "0:03.0"; a span that began before the call root → "−0:02.0".
export function callClock(ms) {
  return `${before(ms) ? "−" : ""}${magnitude(ms)}`;
}

export function activityTime(item) {
  if (item.startMs == null) return "Time not recorded";
  if (item.endMs == null) return callClock(item.startMs);
  return `${callClock(item.startMs)}–${callClock(item.endMs)}`;
}

const spoken = (ms) =>
  `${magnitude(ms)} ${before(ms) ? "before" : "after"} call start`;

function spokenTime(item) {
  const { startMs, endMs } = item;
  if (startMs == null) return "time not recorded";
  if (endMs == null) return `at ${spoken(startMs)}`;
  if (!before(startMs))
    return `from ${magnitude(startMs)} to ${magnitude(endMs)} after call start`;
  return `from ${spoken(startMs)} to ${spoken(endMs)}`;
}

/**
 * One background tool call beside the conversation. Visually secondary to
 * speech; it selects the call's span and never moves the recording, because
 * a trace time is not a recording position.
 */
export function ActivityRow({ item, onSelect }) {
  const kind = item.mcp ? "MCP tool" : "Tool call";
  return (
    <ButtonBase
      onClick={() => onSelect?.(item.spanId)}
      disabled={!onSelect}
      aria-label={`View span ${item.name}, ${kind.toLowerCase()}${item.failed ? ", failed" : ""}, ${spokenTime(item)}`}
      data-testid="call-activity-row"
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
        gap: 0.75,
        width: "100%",
        textAlign: "left",
        pl: 2,
        pr: 1.5,
        py: 0.375,
        bgcolor: "background.neutral",
        borderBottom: "1px dashed",
        borderColor: "divider",
        "&:hover": { bgcolor: "action.hover" },
        "&.Mui-focusVisible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      <Iconify
        icon="solar:settings-minimalistic-linear"
        width={12}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <Typography sx={{ fontSize: 12, color: "text.secondary", flexShrink: 0 }}>
        {kind}
      </Typography>
      <Typography
        sx={{
          fontSize: 12,
          fontFamily: "monospace",
          color: "text.primary",
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {item.name}
      </Typography>
      {item.failed && (
        <Typography sx={{ fontSize: 12, color: "error.main", flexShrink: 0 }}>
          Failed
        </Typography>
      )}
      <Box sx={{ flex: 1 }} />
      <Typography
        sx={{
          fontSize: 12,
          fontFamily: "monospace",
          color: "text.disabled",
          flexShrink: 0,
        }}
      >
        {activityTime(item)}
      </Typography>
    </ButtonBase>
  );
}

export const ACTIVITY_SHAPE = PropTypes.shape({
  key: PropTypes.string.isRequired,
  spanId: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  mcp: PropTypes.bool,
  failed: PropTypes.bool,
  startMs: PropTypes.number,
  endMs: PropTypes.number,
  interleaved: PropTypes.bool,
  anchorTurnId: PropTypes.string,
});

ActivityRow.propTypes = {
  item: ACTIVITY_SHAPE.isRequired,
  onSelect: PropTypes.func,
};

/** Activity that cannot be placed between utterances, timed from the call start. */
export function ActivityGroup({ items, onSelect }) {
  if (!items?.length) return null;
  return (
    <Box component="section" aria-label="Background activity">
      <Typography
        component="h3"
        sx={{
          fontSize: 12,
          fontWeight: 600,
          color: "text.secondary",
          px: 1.25,
          pt: 1,
          pb: 0.5,
        }}
      >
        Background activity · time from call start
      </Typography>
      {items.map((item) => (
        <ActivityRow key={item.key} item={item} onSelect={onSelect} />
      ))}
    </Box>
  );
}

ActivityGroup.propTypes = {
  items: PropTypes.arrayOf(ACTIVITY_SHAPE),
  onSelect: PropTypes.func,
};
