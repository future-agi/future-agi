import React from "react";
import { Box } from "@mui/material";
import GatewayEmptyState from "../components/GatewayEmptyState";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";

const CapabilitiesSection = () => (
  <Box p={3}>
    <SectionHeader
      icon={GATEWAY_ICONS.capabilities}
      title="Capabilities"
      subtitle="What each gateway endpoint and model supports"
    />
    <GatewayEmptyState
      title="No capability data"
      description="This page does not show the gateway's capability report: per-endpoint capabilities, qualification status and managed-traffic eligibility."
    />
  </Box>
);

export default CapabilitiesSection;
