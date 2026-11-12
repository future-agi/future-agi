import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const DataTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway has no data policies: logging is set in Gateway settings."
  />
);

export default DataTab;
