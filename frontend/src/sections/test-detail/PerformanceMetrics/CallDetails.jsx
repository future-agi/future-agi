import React from "react";
import CardWrapper from "./CardWrapper";
import MetricTile from "./MetricTile";
import PropTypes from "prop-types";
import { Stack } from "@mui/material";
import {
  getChatOverrides,
  getIcon,
  getIconColor,
  getLabel,
  getSuffix,
} from "./common";
import { AGENT_TYPES } from "src/sections/agents/constants";

const getMetricConfig = (key, agentType) => {
  if (agentType === AGENT_TYPES.CHAT && getChatOverrides[key]) {
    return getChatOverrides[key];
  }

  return {
    label: getLabel(key),
    icon: getIcon(key),
    iconColor: getIconColor(key),
    suffix: getSuffix(key),
  };
};

const CallDetails = ({ data, expanded, handleSetFilter, agentType }) => {
  const cardTitle =
    agentType === AGENT_TYPES.CHAT ? "Chat Details" : "Call Details";

  const handleCardClick = (key) => {
    if (key === "calls_connected_percentage") return;
    handleSetFilter(key);
  };

  return (
    <CardWrapper expanded={expanded} title={cardTitle}>
      <Stack direction="column" gap={2.5}>
        {Object.entries(data).map(([key, value]) => {
          const { label, icon, iconColor, suffix } = getMetricConfig(
            key,
            agentType,
          );
          const filterable = key !== "calls_connected_percentage";
          return (
            <MetricTile
              key={key}
              label={label}
              value={value}
              suffix={suffix}
              icon={icon}
              iconColor={iconColor}
              subtext={filterable ? "Filters the table below" : undefined}
              onClick={filterable ? () => handleCardClick(key) : undefined}
            />
          );
        })}
      </Stack>
    </CardWrapper>
  );
};

CallDetails.propTypes = {
  data: PropTypes.object.isRequired,
  expanded: PropTypes.bool,
  handleSetFilter: PropTypes.func.isRequired,
  agentType: PropTypes.string.isRequired,
};

export default CallDetails;
