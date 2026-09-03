import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Collapse from "@mui/material/Collapse";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  alignToPlan,
  classifySteps,
  formatElapsed,
  humanize,
  RETRIED,
  trailSummary,
} from "../helpers/toolTrail";
import ToolCallCard from "./ToolCallCard";

// One line for a whole run: what it is doing now, then what it cost, steps on click.
export default function ThinkingTrail({
  toolCalls,
  isStreaming,
  plan,
  planRun,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [expanded, setExpanded] = useState(false);
  const startedAt = useRef(null);
  const [elapsed, setElapsed] = useState(null);

  const steps = classifySteps(toolCalls);
  const { total, failed, current } = trailSummary(steps);
  const live = isStreaming && Boolean(current);

  const aligned = plan?.length ? alignToPlan(planRun || toolCalls, plan) : null;
  // A run that matched none of the declared flow is not evidence of the flow,
  // so the trail claims nothing rather than claiming the wrong thing.
  const flow = aligned && aligned.done > 0 ? aligned : null;

  useEffect(() => {
    if (isStreaming && startedAt.current === null) {
      startedAt.current = Date.now();
    }
    if (!isStreaming && startedAt.current !== null && elapsed === null) {
      setElapsed(Date.now() - startedAt.current);
    }
  }, [isStreaming, elapsed]);

  if (!total) return null;

  const liveLabel = () => {
    const activity = humanize(current.tool_name);
    const at = flow?.byCallId[current.call_id];
    if (!at) return activity;
    if (at.planIndex === null) return `${activity} · extra step`;
    const where = `step ${at.planIndex + 1} of ${flow.planned}`;
    return at.planKind === "revisit"
      ? `${activity} · ${where}, again`
      : `${activity} · ${where}`;
  };

  const label = live
    ? liveLabel()
    : [
        elapsed ? `Thought for ${formatElapsed(elapsed)}` : "Thought",
        flow
          ? `${flow.done} of ${flow.planned} steps`
          : `${total} step${total === 1 ? "" : "s"}`,
        flow?.extra ? `${flow.extra} extra` : null,
        failed ? `${failed} skipped` : null,
      ]
        .filter(Boolean)
        .join(" · ");

  return (
    <Box sx={{ mb: 1 }}>
      <Box
        onClick={() => setExpanded((prev) => !prev)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          py: 0.75,
          cursor: "pointer",
          userSelect: "none",
          color: "text.disabled",
          "&:hover": { color: "text.secondary" },
        }}
      >
        <Iconify
          icon={live ? "mdi:loading" : "mdi:brain"}
          width={15}
          sx={
            live
              ? {
                  animation: "falcon-trail-spin 1s linear infinite",
                  "@keyframes falcon-trail-spin": {
                    to: { transform: "rotate(360deg)" },
                  },
                }
              : undefined
          }
        />
        <Typography
          variant="body2"
          sx={{
            fontSize: 13,
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            ...(live && {
              animation: "falcon-trail-pulse 1.6s ease-in-out infinite",
              "@keyframes falcon-trail-pulse": {
                "0%, 100%": { opacity: 0.55 },
                "50%": { opacity: 1 },
              },
            }),
          }}
        >
          {label}
        </Typography>
        <Iconify
          icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={16}
        />
      </Box>

      <Collapse in={expanded} unmountOnExit>
        <Box
          sx={{
            pl: 1.5,
            ml: 0.75,
            borderLeft: 1,
            borderColor: isDark
              ? alpha(theme.palette.common.white, 0.08)
              : alpha(theme.palette.common.black, 0.08),
          }}
        >
          {steps.map((step) => {
            const at = flow?.byCallId[step.call_id];
            const card = (
              <ToolCallCard
                toolCall={
                  step.outcome === RETRIED
                    ? { ...step, status: RETRIED }
                    : step
                }
              />
            );
            return (
              <Box
                key={step.call_id}
                sx={{
                  opacity: step.outcome === RETRIED ? 0.5 : 1,
                  ...(flow && {
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 1,
                  }),
                }}
              >
                {flow && (
                  <Typography
                    variant="caption"
                    sx={{
                      width: 34,
                      flexShrink: 0,
                      pt: 1.15,
                      fontSize: 11,
                      textAlign: "right",
                      color: "text.disabled",
                    }}
                  >
                    {at && at.planIndex !== null ? at.planIndex + 1 : "extra"}
                  </Typography>
                )}
                {flow ? <Box sx={{ flex: 1, minWidth: 0 }}>{card}</Box> : card}
              </Box>
            );
          })}

          {flow?.pending.length > 0 && (
            <Box sx={{ pb: 1 }}>
              <Typography
                variant="caption"
                sx={{
                  display: "block",
                  mb: 0.5,
                  fontSize: 11,
                  color: "text.disabled",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {isStreaming ? "Still to come" : "Declared, not run"}
              </Typography>
              {flow.pending.map((p) => (
                <Box
                  key={p.index}
                  sx={{
                    display: "flex",
                    gap: 1,
                    alignItems: "center",
                    py: 0.4,
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{
                      width: 34,
                      flexShrink: 0,
                      fontSize: 11,
                      textAlign: "right",
                      color: "text.disabled",
                    }}
                  >
                    {p.index + 1}
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{ fontSize: 12, color: "text.disabled", opacity: 0.7 }}
                  >
                    {humanize(p.tool)}
                  </Typography>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}

ThinkingTrail.propTypes = {
  toolCalls: PropTypes.arrayOf(PropTypes.object).isRequired,
  isStreaming: PropTypes.bool,
  // Ordered tool names the active skill declared for this turn.
  plan: PropTypes.arrayOf(PropTypes.string),
  // Every tool call in the turn, when this trail is one of several. The flow
  // belongs to the turn, so progress cannot be counted from one segment alone.
  planRun: PropTypes.arrayOf(PropTypes.object),
};
