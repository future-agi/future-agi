import React from "react";
import PropTypes from "prop-types";
import { Alert } from "@mui/material";
import {
  useAgentDetail,
  useProviderConfigurationHealth,
} from "src/api/agents/agentDetail";
import { describeConfigurationHealth } from "./providerConnectionHealth";

// Shows why scheduled call imports for this agent's provider connection are
// failing. Nothing renders while imports are healthy or observability is off.
const ProviderConnectionHealth = ({ agentDefinitionId }) => {
  const { data: agent } = useAgentDetail(agentDefinitionId);
  // A turned-off provider is never polled, so its last recorded problem is
  // never cleared; only a live connection's health means anything.
  const { data: health } = useProviderConfigurationHealth(
    agent?.observabilityEnabled ? agent.observabilityProviderId : null,
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
