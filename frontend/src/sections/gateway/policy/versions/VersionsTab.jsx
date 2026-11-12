import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const VersionsTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway keeps no policy versions."
  />
);

export default VersionsTab;
