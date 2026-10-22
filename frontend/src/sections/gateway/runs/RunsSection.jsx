import React from "react";
import { Box } from "@mui/material";
import GatewayEmptyState from "../components/GatewayEmptyState";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";

const RunsSection = () => (
  <Box p={3}>
    <SectionHeader
      icon={GATEWAY_ICONS.runs}
      title="Runs"
      subtitle="Agent runs that called the gateway, with their steps, models and spend"
    />
    <GatewayEmptyState
      title="Not in this release"
      description="The run list, with each run's step tree, model breakdown, call timeline and budget burn."
    />
  </Box>
);

export default RunsSection;
