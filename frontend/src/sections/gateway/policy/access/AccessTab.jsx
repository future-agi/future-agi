import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const AccessTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway has no access policies: keys, providers and models are set on their own pages."
  />
);

export default AccessTab;
