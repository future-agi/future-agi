import React from "react";
import PropTypes from "prop-types";
import { Alert } from "@mui/material";
import {
  useAgentDetail,
  useProviderConfigurationHealth,
} from "src/api/agents/agentDetail";
import { describeConfigurationHealth } from "./providerConnectionHealth";

// Shows why scheduled call imports for this agent's provider connection are
// failing. Nothing renders while imports are healthy.
const ProviderConnectionHealth = ({ agentDefinitionId }) => {
  const { data: agent } = useAgentDetail(agentDefinitionId);
  const { data: health } = useProviderConfigurationHealth(
    agent?.observabilityProviderId,
  );
  const message = describeConfigurationHealth(health);
  if (!message) return null;
  return (
    <Alert severity="warning" sx={{ mt: 1 }} data-testid="provider-health">
      {message}
    </Alert>
  );
};

ProviderConnectionHealth.propTypes = {
  agentDefinitionId: PropTypes.string,
};

export default ProviderConnectionHealth;
