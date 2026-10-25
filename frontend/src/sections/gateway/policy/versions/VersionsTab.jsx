import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const VersionsTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Every policy version, with a diff against the active one and a rollback."
  />
);

export default VersionsTab;
