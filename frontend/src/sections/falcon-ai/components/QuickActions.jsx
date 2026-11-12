import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { isPrototypeMode } from "src/utils/prototype";

// Each prompt must work when sent unedited: the trace drawer's Imagine panel
// sends on click, and a new workspace may have no traces, agents or data yet.
// So no blanks to fill in; the assistant asks for what it needs, and each
// prompt says what to do when there is no data.
const QUICK_ACTIONS = [
  {
    icon: "solar:danger-circle-linear",
    label: "Analyse my error feed",
    prompt:
      "Summarise the errors in my error feed from the last 7 days. Group similar errors, explain the likely cause of each group and suggest a fix. If there are no errors or traces yet, tell me how to start sending traces.",
  },
  {
    icon: "solar:widget-4-linear",
    label: "Create an Imagine view",
    // Only the trace drawer's Imagine tab can build a view, so elsewhere the
    // prompt names the Imagine button before any questions are asked.
    prompt:
      "Help me create an Imagine view that shows the parts of a trace I care about at a glance. If you can't build it from this chat, tell me to open a trace and click Imagine in its detail view. Otherwise ask me what I want to see. If I have no traces yet, tell me how to send my first one.",
  },
  {
    icon: "solar:database-linear",
    label: "Build a dataset",
    prompt:
      "Help me build a dataset from my traces. First ask me which time range and what kind of traces to include. If I have no traces yet, tell me how to start sending them, or another way to create a dataset, such as uploading a file.",
  },
  {
    icon: "solar:checklist-minimalistic-linear",
    label: "Create an evaluation",
    prompt:
      "Help me create an evaluation for my AI agent. First ask me what the agent does and what I want to check, such as accuracy or tone. Then suggest an evaluation and what to run it on. If I don't have data to run it on yet, tell me what I need.",
  },
  {
    icon: "solar:play-circle-linear",
    label: "Run simulation for my agent",
    prompt:
      "Help me run a simulation for my agent. First ask me which agent to test and what situations to cover. If I have no agent set up yet, tell me how to add one.",
  },
];

// The settled prototype-only starter action is removed; real actions are unchanged.

export default function QuickActions({ onAction }) {
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
            borderColor: "border.default",
            bgcolor: "transparent",
            transition: "all 0.15s ease",
            "&:hover": {
              bgcolor: "action.hover",
              borderColor: "border.default",
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
