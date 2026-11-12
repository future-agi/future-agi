/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import {
  PIPELINE,
  TIMELINE_LABEL,
  pipelineStates,
  pipelineTone,
  statusMeta,
} from "../lib/status";
import { formatDateTime } from "../lib/format";

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

// `stalled`: this server runs no Hub checker (lib/uploadsChecked.js), so a
// "checking" submission is not being checked. Its chip then names the step it
// waits after, "Received", never "Checks running"; `data-status` keeps the
// server's state.
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

// What happened, in order, from the submission's audit events.
export default function StatusTimeline({ timeline }) {
  if (!timeline?.length) return null;
  return (
    <Box
      component="ol"
      data-testid="hub-timeline"
      sx={{ m: 0, p: 0, listStyle: "none", display: "grid", gap: 0.75 }}
    >
      {timeline.map((event, index) => (
        <Box
          component="li"
          key={`${event.action}-${index}`}
          sx={{ display: "flex", gap: 1.5, alignItems: "baseline" }}
        >
          <Typography
            typography="s3"
            color="text.tertiary"
            sx={{ width: 110, flexShrink: 0 }}
          >
            {formatDateTime(event.at)}
          </Typography>
          <Typography typography="s2" color="text.primary">
            {TIMELINE_LABEL[event.action] || event.action}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

StatusTimeline.propTypes = { timeline: PropTypes.array };
