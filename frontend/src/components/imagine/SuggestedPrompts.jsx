import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase } from "@mui/material";
import Iconify from "src/components/iconify";

const PROMPTS = [
  { label: "Show me where the latency is", icon: "solar:clock-circle-linear" },
  { label: "Visualize token usage by span", icon: "solar:chart-2-linear" },
  { label: "Show the agent execution flow", icon: "solar:graph-linear" },
  {
    label: "What's the cost breakdown?",
    icon: "solar:dollar-minimalistic-linear",
  },
  { label: "Compare the LLM calls", icon: "solar:transfer-horizontal-linear" },
  { label: "Summarize this trace", icon: "solar:notes-linear" },
];

export default function SuggestedPrompts({ onSelect, prompts }) {
  const items = prompts || PROMPTS;
  return (
    <Box
      sx={{
        display: "flex",
        flexWrap: "wrap",
        gap: 0.75,
        justifyContent: "center",
        maxWidth: 480,
      }}
    >
      {items.map((p) => (
        <ButtonBase
          key={p.label}
          onClick={() => onSelect(p.label)}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 1.25,
            py: 0.5,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            fontSize: 12,
            fontWeight: 400,
            color: "text.secondary",
            whiteSpace: "nowrap",
            transition: "all 150ms",
            "&:hover": {
              bgcolor: "action.hover",
              borderColor: "primary.main",
              color: "primary.main",
            },
          }}
        >
          <Iconify icon={p.icon} width={14} />
          <span>{p.label}</span>
        </ButtonBase>
      ))}
    </Box>
  );
}

SuggestedPrompts.propTypes = {
  onSelect: PropTypes.func.isRequired,
  prompts: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, icon: PropTypes.string }),
  ),
};
