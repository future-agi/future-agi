import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const PresetsTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway has no routing presets: routing is set on the Routing page."
  />
);

export default PresetsTab;
