import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Collapse from "@mui/material/Collapse";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  classifySteps,
  formatElapsed,
  humanize,
  trailSummary,
} from "../helpers/toolTrail";
import ToolCallCard from "./ToolCallCard";

/**
 * One line for a whole run of tool calls.
 *
 * While the turn is live it shows what the agent is doing right now, one line,
 * so a seventy step run does not push the answer off the screen. When the turn
 * finishes the line collapses to what it cost, and the steps are still there
 * on click.
 */
export default function ThinkingTrail({ toolCalls, isStreaming }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [expanded, setExpanded] = useState(false);
  const startedAt = useRef(null);
  const [elapsed, setElapsed] = useState(null);

  const steps = classifySteps(toolCalls);
  const { total, failed, current } = trailSummary(steps);
  const live = isStreaming && Boolean(current);

  useEffect(() => {
    if (isStreaming && startedAt.current === null) {
      startedAt.current = Date.now();
    }
    if (!isStreaming && startedAt.current !== null && elapsed === null) {
      setElapsed(Date.now() - startedAt.current);
    }
  }, [isStreaming, elapsed]);

  if (!total) return null;

  const label = live
    ? humanize(current.tool_name)
    : [
        elapsed ? `Thought for ${formatElapsed(elapsed)}` : "Thought",
        `${total} step${total === 1 ? "" : "s"}`,
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
          {steps.map((step) => (
            <Box
              key={step.call_id}
              sx={{ opacity: step.outcome === "retried" ? 0.5 : 1 }}
            >
              <ToolCallCard
                toolCall={
                  step.outcome === "retried"
                    ? { ...step, status: "retried" }
                    : step
                }
              />
            </Box>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

ThinkingTrail.propTypes = {
  toolCalls: PropTypes.arrayOf(PropTypes.object).isRequired,
  isStreaming: PropTypes.bool,
};
