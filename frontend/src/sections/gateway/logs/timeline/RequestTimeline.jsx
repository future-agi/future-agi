import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { formatMs } from "../logStatus";
import { buildTimeline } from "./buildTimeline";

// One request as a waterfall: each step on the request's own time axis, the
// offset from the start on the left, the bar placed and sized by the recorded
// times. Colours are the theme's status colours.

const TONE_COLOR = {
  healthy: "success.main",
  warning: "warning.main",
  error: "error.main",
  neutral: "text.disabled",
};

const pct = (value, total) =>
  `${Math.min(100, Math.max(0, (value / total) * 100)).toFixed(2)}%`;

function Track({ event, totalMs }) {
  const color = TONE_COLOR[event.tone] || TONE_COLOR.neutral;
  const hasBar = event.durationMs != null && event.offsetMs != null;
  return (
    <Box
      aria-hidden
      sx={{
        position: "relative",
        height: 8,
        borderRadius: 0.5,
        bgcolor: "action.hover",
      }}
    >
      {hasBar ? (
        <Box
          data-bar
          style={{
            left: pct(event.offsetMs, totalMs),
            width: pct(Math.max(event.durationMs, totalMs * 0.004), totalMs),
          }}
          sx={{
            position: "absolute",
            top: 0,
            bottom: 0,
            borderRadius: 0.5,
            bgcolor: color,
          }}
        />
      ) : (
        event.offsetMs != null && (
          <Box
            data-point
            style={{ left: pct(event.offsetMs, totalMs) }}
            sx={{
              position: "absolute",
              top: -1,
              width: 10,
              height: 10,
              ml: "-5px",
              borderRadius: "50%",
              border: "2px solid",
              borderColor: color,
              bgcolor: "background.paper",
            }}
          />
        )
      )}
    </Box>
  );
}

Track.propTypes = {
  event: PropTypes.object.isRequired,
  totalMs: PropTypes.number.isRequired,
};

export default function RequestTimeline({ log }) {
  const { events, totalMs } = buildTimeline(log);
  if (events.length === 0) return null;
  const axis = Math.max(totalMs, 1);

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          mb: 1,
        }}
      >
        <Typography
          component="h3"
          typography="s1"
          fontWeight="fontWeightMedium"
        >
          Timeline
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {formatMs(totalMs)} end to end
        </Typography>
      </Box>
      <Box
        component="ol"
        aria-label="Request timeline"
        sx={{ listStyle: "none", m: 0, p: 0, display: "grid", gap: 0.25 }}
      >
        {events.map((event, i) => (
          <Box
            component="li"
            key={`${event.kind}-${event.index ?? i}`}
            data-kind={event.kind}
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "56px minmax(0, 1fr)",
                sm: "56px minmax(0, 1fr) minmax(120px, 34%)",
              },
              columnGap: 1.5,
              rowGap: 0.5,
              alignItems: "center",
              py: 0.75,
              borderTop: i === 0 ? 0 : "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}
            >
              {event.offsetMs != null ? `+${formatMs(event.offsetMs)}` : "—"}
            </Typography>
            <Box sx={{ minWidth: 0 }}>
              <Typography
                typography="s2"
                color="text.primary"
                sx={{ overflowWrap: "anywhere" }}
              >
                {event.label}
              </Typography>
              {(event.detail || event.durationMs != null || event.billable) && (
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {[
                    event.detail,
                    event.durationMs != null && formatMs(event.durationMs),
                    event.billable && "Billed",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
              )}
            </Box>
            <Box sx={{ gridColumn: { xs: "2", sm: "3" } }}>
              <Track event={event} totalMs={axis} />
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

RequestTimeline.propTypes = { log: PropTypes.object.isRequired };
