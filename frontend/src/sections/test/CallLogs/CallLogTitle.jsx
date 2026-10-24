import { Box, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { CallType } from "./common";
import Iconify from "src/components/iconify";
import { AGENT_TYPES } from "src/sections/agents/constants";
import SvgColor from "src/components/svg-color";

const getIconForLogs = (type) => {
  if (type === AGENT_TYPES.CHAT) {
    return (
      <SvgColor src="/assets/icons/ic_chat_single.svg" width={18} height={18} />
    );
  }

  if (["inbound", "Inbound", "INBOUND"].includes(type)) {
    return (
      <SvgColor
        src="/assets/icons/ic_call_inbound.svg"
        width={18}
        height={18}
      />
    );
  }

  if (["outbound", "Outbound", "OUTBOUND"].includes(type)) {
    return (
      <SvgColor
        src="/assets/icons/ic_call_outbound.svg"
        width={18}
        height={18}
      />
    );
  }

  // ✅ fallback icon (previously missing return)
  return (
    <Iconify
      icon="solar:chat-round-line-linear"
      sx={{ width: 20, height: 20, color: "status.success.fg" }}
    />
  );
};

const CallLogTitle = ({ callType, simulationCallType }) => {
  const isChat = simulationCallType === AGENT_TYPES.CHAT;

  const iconType = isChat ? simulationCallType : callType;

  const title = isChat
    ? "Chat Details"
    : callType === CallType.INBOUND
      ? "Inbound Call"
      : "Outbound Call";

  return (
    <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
      {getIconForLogs(iconType)}
      <Typography typography="s1" fontWeight="fontWeightMedium">
        {title}
      </Typography>
    </Box>
  );
};

CallLogTitle.propTypes = {
  callType: PropTypes.string,
  simulationCallType: PropTypes.string,
};

export default CallLogTitle;
