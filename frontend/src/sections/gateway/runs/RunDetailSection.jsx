import React from "react";
import { Box } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import GatewayEmptyState from "../components/GatewayEmptyState";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";

const RunDetailSection = () => {
  const { runId } = useParams();
  const navigate = useNavigate();

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.runs}
        title="Run"
        subtitle={runId}
        actions={[
          {
            label: "All runs",
            icon: "mdi:arrow-left",
            onClick: () => navigate(paths.dashboard.gateway.runs),
          },
        ]}
      />
      <GatewayEmptyState
        title="Not in this release"
        description="This run's step tree, model breakdown, call timeline and budget burn."
      />
    </Box>
  );
};

export default RunDetailSection;
