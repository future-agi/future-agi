import React from "react";
import PropTypes from "prop-types";
import { AGIChip } from "../../chip/AGIChip";
import { Box } from "@mui/material";
import { AGENT_TYPES } from "../../../sections/agents/constants";

const getAgentTypeLabel = (agentType, isInbound) => {
  if (agentType === AGENT_TYPES.VOICE) {
    return isInbound ? "Voice Inbound" : "Voice Outbound";
  } else {
    return "Chat";
  }
};

const AgentTypeCellRenderer = ({ data }) => {
  const agentType = data?.agentType;
  const isInbound = data.inbound;

  return (
    <Box height={"100%"} display={"flex"} alignItems={"center"}>
      <AGIChip
        label={getAgentTypeLabel(agentType, isInbound)}
        size="small"
        sx={{
          backgroundColor: "background.neutral",
          borderRadius: "4px",
          border: "1px solid",
          borderColor: "divider",
          color: "text.primary",
          px: 1,
          pt: 0.5,
          pb: 0.375,
          "& .MuiChip-label": {
            justifyContent: "flex-start",
            width: "100%",
            textAlign: "left",
            padding: 0,
          },
          pointerEvents: "none",
        }}
      />
    </Box>
  );
};

AgentTypeCellRenderer.propTypes = {
  data: PropTypes.object,
};

export default AgentTypeCellRenderer;
