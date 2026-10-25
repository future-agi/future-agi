import React from "react";
import { Box } from "@mui/material";
import GatewayEmptyState from "../components/GatewayEmptyState";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";

const PlaygroundSection = () => (
  <Box p={3}>
    <SectionHeader
      icon={GATEWAY_ICONS.playground}
      title="Playground"
      subtitle="Requests sent through the gateway, with how each was routed and billed"
    />
    <GatewayEmptyState
      title="Not in this release"
      description="A request form with agent and routing options, and the response with its cost, routing headers, attempts and evidence."
    />
  </Box>
);

export default PlaygroundSection;
