/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import {
  PIPELINE,
  TIMELINE_LABEL,
  pipelineStates,
  pipelineTone,
  statusMeta,
} from "../lib/status";
import { formatStamp } from "../lib/decision";
import { timeAgo } from "../lib/format";

import HubIcon from "./HubIcon";
import ToneChip, { toneColor } from "./ToneChip";

const DOT = {
  done: { icon: "check" },
  active: { icon: "clock" },
  failed: { icon: "alert" },
  stopped: { icon: "close" },
  todo: { icon: null },
};
const dotColor = (dot) => (theme) => toneColor(theme, dot.tone);

// The four-step pipeline: Received → Automated checks → Maintainer review →
// Published. With no status it shows the steps as a plain guide.
export function StepTracker({ status, compact }) {
  const states = status ? pipelineStates(status) : PIPELINE.map(() => "todo");
  return (
    <Box
      component="ol"
      data-testid="hub-step-tracker"
      data-status={status || "none"}
      sx={{
        m: 0,
        p: 0,
        listStyle: "none",
        display: "grid",
        gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, minmax(0, 1fr))" },
        gap: compact ? 1 : 1.5,
      }}
    >
      {PIPELINE.map((step, index) => {
        const state = states[index];
        const dot = { ...DOT[state], tone: pipelineTone(state, status) };
        return (
          <Box
            component="li"
            key={step.key}
            data-state={state}
            sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}
          >
            <Box
              sx={{
                width: 24,
                height: 24,
                flexShrink: 0,
                borderRadius: "50%",
                border: "1.5px solid",
                borderColor: dotColor(dot),
                color: dotColor(dot),
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                typography: "s3",
                fontWeight: 600,
                bgcolor:
                  state === "active" ? "action.selected" : "background.paper",
              }}
            >
              {dot.icon ? <HubIcon name={dot.icon} size={13} /> : index + 1}
            </Box>
            <Typography
              typography="s2"
              fontWeight={state === "active" || state === "failed" ? 600 : 500}
              color={state === "todo" ? "text.tertiary" : "text.primary"}
              sx={{ minWidth: 0 }}
            >
              {step.label}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}

StepTracker.propTypes = { status: PropTypes.string, compact: PropTypes.bool };

// `stalled`: no Hub checker is running, so a "checking" submission is not
// being checked: this server runs none (lib/uploadsChecked.js), or its
// checker is configured but not running, as the server's answer says
// (checksStopped). Its chip then names the step it waits after, "Received",
// never "Checks running"; `data-status` keeps the server's state.
export function StatusChip({ status, stalled = false }) {
  const meta = statusMeta(status);
  const label =
    stalled && status === "checking"
      ? statusMeta("received").label
      : meta.label;
  return (
    <ToneChip
      tone={meta.tone}
      label={label}
      testId="hub-status-chip"
      data-status={status}
    />
  );
}

StatusChip.propTypes = { status: PropTypes.string, stalled: PropTypes.bool };

const TIME_SX = {
  position: "relative",
  zIndex: 1,
  whiteSpace: "nowrap",
  borderRadius: 0.5,
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: 2,
  },
};

// A moment in words ("4 min ago"), with the exact moment and its time zone
// (formatStamp, as the review queue's wait reads it) in a tooltip on hover
// and on keyboard focus. `label` names what happened then ("Updated"); the
// accessible name says both. It takes its colour and size from the text
// around it, and sits above a row's stretched link so it can take focus.
export function TimeAgo({ iso, label }) {
  const stamp = formatStamp(iso);
  if (!stamp) return <span>—</span>;
  const ago = timeAgo(iso);
  return (
    <Tooltip title={`${label} ${stamp}`} describeChild>
      <Box
        component="time"
        dateTime={iso}
        tabIndex={0}
        aria-label={`${label} ${ago}, ${stamp}`}
        sx={TIME_SX}
      >
        {ago}
      </Box>
    </Tooltip>
  );
}

TimeAgo.propTypes = {
  iso: PropTypes.string,
  label: PropTypes.string.isRequired,
};

// Each audit event's mark on the history rail, in the submission's tones.
const EVENT_TONE = {
  "submission.received": "working",
  "submission.withdrawn": "stopped",
  "submission.superseded": "stopped",
  "check.claimed": "working",
  "check.passed": "success",
  "check.failed": "error",
  "check.errored": "error",
  "review.approved": "success",
  "review.rejected": "stopped",
  "review.changes_requested": "attention",
};

/**
 * How long after `fromIso` the moment `toIso` came, in the Hub's short
 * words ("7 s later", "21 min later"), or "" when either is unreadable.
 * @param {string} fromIso
 * @param {string} toIso
 */
export function laterText(fromIso, toIso) {
  const ms = Date.parse(toIso) - Date.parse(fromIso);
  if (!Number.isFinite(ms) || ms < 0) return "";
  const seconds = Math.round(ms / 1000);
  if (seconds < 1) return "less than a second later";
  if (seconds < 60) return `${seconds} s later`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min later`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h later`;
  return `${Math.round(hours / 24)} days later`;
}

// The rail from an event's mark down to the next event's.
const RAIL = {
  "&::before": {
    content: '""',
    position: "absolute",
    left: 5.5,
    top: 16,
    bottom: 0,
    width: "1px",
    bgcolor: "divider",
  },
};

// What happened, in order, from the submission's audit events: each event
// on a rail in its tone, with the exact moment and its time zone, and how
// long after the event before it came.
export default function StatusTimeline({ timeline }) {
  if (!timeline?.length) return null;
  return (
    <Box
      component="ol"
      data-testid="hub-timeline"
      sx={{ m: 0, p: 0, listStyle: "none", display: "grid" }}
    >
      {timeline.map((event, index) => {
        const last = index === timeline.length - 1;
        const later = index ? laterText(timeline[index - 1].at, event.at) : "";
        const tone = EVENT_TONE[event.action] || "neutral";
        return (
          <Box
            component="li"
            key={`${event.action}-${index}`}
            data-action={event.action}
            sx={{
              position: "relative",
              display: "grid",
              gridTemplateColumns: "12px minmax(0, 1fr)",
              columnGap: 1.25,
              pb: last ? 0 : 1.5,
              ...(last ? {} : RAIL),
            }}
          >
            <Box
              aria-hidden="true"
              sx={{
                width: 12,
                height: 12,
                mt: 0.5,
                borderRadius: "50%",
                border: "2px solid",
                borderColor: (theme) => toneColor(theme, tone),
                bgcolor: "background.paper",
              }}
            />
            <Box
              sx={{
                minWidth: 0,
                display: "flex",
                flexWrap: "wrap",
                alignItems: "baseline",
                columnGap: 1.5,
                rowGap: 0.25,
              }}
            >
              <Typography typography="s2" fontWeight={500} color="text.primary">
                {TIMELINE_LABEL[event.action] || event.action}
              </Typography>
              <Typography typography="s3" color="text.tertiary">
                <Box component="time" dateTime={event.at}>
                  {formatStamp(event.at) || "—"}
                </Box>
                {later && ` · ${later}`}
              </Typography>
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}

StatusTimeline.propTypes = { timeline: PropTypes.array };
