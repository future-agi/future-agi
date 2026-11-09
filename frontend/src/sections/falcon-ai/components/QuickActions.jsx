import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { isPrototypeMode } from "src/utils/prototype";

const QUICK_ACTIONS = [
  {
    icon: "mdi:alert-circle-outline",
    label: "Analyse my error feed",
    prompt:
      "Analyse the errors in my error feed from [time range — e.g. last 24 hours, last 7 days]. Focus on [scope — e.g. a specific project, agent, or error type]. Group similar failures together, identify the root cause for each group from the trace context, and suggest the most likely fix for each one. Prioritise by [what matters most — e.g. trace impact, recency, severity].",
  },
  {
    icon: "mdi:view-dashboard-outline",
    label: "Create an Imagine view",
    prompt:
      "Open a recent trace and create a new Imagine view in the trace detail drawer. Surface [which span attributes you want to see — e.g. system prompt, user input, tool calls, retrieved context, model output, latency] in a [layout — e.g. side-by-side, stacked, table] so I can [what you're trying to check at a glance]. Save it so I can reuse it across traces.",
  },
  {
    icon: "mdi:database-outline",
    label: "Build a dataset",
    prompt:
      "Build a dataset from my traces in [time range — e.g. last 7 days, this month]. Filter to traces where [filter criteria — e.g. user input mentions a keyword, latency exceeds X, evaluation score below Y]. Pull [which fields to extract — e.g. user message, retrieved context, tool calls, final response] into separate columns. I want to use it for [purpose — e.g. regression testing, fine-tuning, eval baseline].",
  },
  {
    icon: "mdi:clipboard-check-outline",
    label: "Create an evaluation",
    prompt:
      "Create an evaluation that checks [what quality you want to measure — e.g. groundedness, factual accuracy, tone, format compliance, instruction following]. My agent does [brief description of what the agent does]. Score each response from 0 to 1 and flag responses that fail because [the failure mode you care about most]. Run it on [scope — e.g. last 100 traces, a specific dataset].",
  },
  {
    icon: "mdi:play-circle-outline",
    label: "Run simulation for my agent",
    prompt:
      "Run a simulation against [which agent — e.g. my support agent, the checkout assistant]. Generate around [number] multi-turn conversations covering [scenarios you want to stress-test — e.g. happy path, edge cases, adversarial inputs, specific topics]. Show me which conversations the agent failed to resolve and what the common failure modes look like.",
  },
];

// The settled prototype-only starter action is removed; real actions are unchanged.

export default function QuickActions({ onAction }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const actions = isPrototypeMode() ? [] : QUICK_ACTIONS;

  return (
    <Box
      sx={{
        display: "flex",
        flexWrap: "wrap",
        gap: 1,
        justifyContent: "center",
        maxWidth: 520,
        mx: "auto",
      }}
    >
      {actions.map((action) => (
        <ButtonBase
          key={action.label}
          onClick={() => onAction?.(action.prompt)}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 2,
            py: 1,
            borderRadius: "12px",
            border: 1,
            borderColor: isDark
              ? alpha(theme.palette.common.white, 0.1)
              : alpha(theme.palette.common.black, 0.1),
            bgcolor: "transparent",
            transition: "all 0.15s ease",
            "&:hover": {
              bgcolor: isDark
                ? alpha(theme.palette.common.white, 0.04)
                : alpha(theme.palette.common.black, 0.03),
              borderColor: isDark
                ? alpha(theme.palette.common.white, 0.2)
                : alpha(theme.palette.common.black, 0.2),
            },
          }}
        >
          <Iconify
            icon={action.icon}
            width={16}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <Typography
            variant="body2"
            sx={{
              fontSize: 13,
              color: "text.secondary",
              whiteSpace: "nowrap",
            }}
          >
            {action.label}
          </Typography>
        </ButtonBase>
      ))}
    </Box>
  );
}

QuickActions.propTypes = {
  onAction: PropTypes.func,
};
