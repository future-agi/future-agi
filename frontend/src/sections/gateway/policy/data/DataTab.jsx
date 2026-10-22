import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const DataTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="How much request and response content is logged, how long logs are kept, and whether providers may train on your data."
  />
);

export default DataTab;
