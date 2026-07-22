import React from "react";
import { Box, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";

// Agent type configs (used in scenarios and agent-definitions)
const AGENT_TYPE_CONFIG = {
  text: { label: "Chat", icon: "/assets/icons/ic_chat_single.svg" },
  prompt: { label: "Prompt", icon: "/assets/icons/navbar/ic_prompt.svg" },
  chat: { label: "Chat", icon: "/assets/icons/ic_chat_single.svg" },
  voice: {
    label: "Voice",
    icon: "/assets/icons/ic_phone_call.svg",
  },
  inbound: {
    label: "Voice Inbound",
    icon: "/assets/icons/ic_phone_call.svg",
  },
  outbound: {
    label: "Voice Outbound",
    icon: "/assets/icons/ic_phone_call.svg",
  },
  voice_inbound: {
    label: "Voice Inbound",
    icon: "/assets/icons/ic_phone_call.svg",
  },
  voice_outbound: {
    label: "Voice Outbound",
    icon: "/assets/icons/ic_phone_call.svg",
  },
};

// Scenario type configs
const SCENARIO_TYPE_CONFIG = {
  script: { label: "Script", icon: "/assets/icons/components/ic_script.svg" },
  dataset: {
    label: "Dataset",
    icon: "/assets/icons/navbar/hugeicons.svg",
  },
  graph: { label: "Graph", icon: "/assets/icons/navbar/ic_sessions.svg" },
};

// All configs merged for flat lookup
const CHIP_CONFIG = {
  ...AGENT_TYPE_CONFIG,
  ...SCENARIO_TYPE_CONFIG,
};

/**
 * Resolve chip config from a key.
 * @param {string} key - lookup key (case-insensitive)
 * @returns {{ label: string, icon: string|null }}
 */
export const getChipConfig = (key) => {
  const normalized = key?.toLowerCase();
  return CHIP_CONFIG[normalized] || { label: key || "Unknown", icon: null };
};

/**
 * Reusable chip cell renderer for AG Grid.
 *
 * Simple usage (value-based):
 *   { field: "scenarioType", cellRenderer: ChipCellRenderer }
 *
 * Custom key resolution (when the lookup key depends on multiple data fields):
 *   {
 *     field: "inbound",
 *     cellRenderer: ChipCellRenderer,
 *     cellRendererParams: {
 *       resolveKey: (data) => data.agentType === "voice"
 *         ? (data.inbound ? "voice_inbound" : "voice_outbound")
 *         : "chat",
 *     },
 *   }
 */
const ChipCellRenderer = ({ value, data, colDef }) => {
  const resolveKey = colDef?.cellRendererParams?.resolveKey;
  const key = resolveKey ? resolveKey(data) : value;
  const config = getChipConfig(key);

  return (
    <Box height="100%" display="flex" alignItems="center">
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.25,
          padding: (theme) => theme.spacing(0.25, 1.5),
        }}
      >
        {config.icon && (
          <SvgColor
            src={config.icon}
            sx={{ width: 16, height: 16, color: "text.primary" }}
          />
        )}
        <Typography typography="s3" fontWeight="fontWeightMedium">
          {config.label}
        </Typography>
      </Box>
    </Box>
  );
};

ChipCellRenderer.propTypes = {
  value: PropTypes.any,
  data: PropTypes.object,
  colDef: PropTypes.object,
};

export default ChipCellRenderer;
