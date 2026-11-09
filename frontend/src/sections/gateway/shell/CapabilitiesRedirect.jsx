import React from "react";

import { paths } from "src/routes/paths";

import { useManagedModelsStatus } from "../models/hooks/useManagedModels";
import GatewayRedirect from "./GatewayRedirect";
import { hiddenPageRedirect } from "./page-status";

// The hidden capabilities URL opens the nearest page that works: the Catalog
// (hiddenPageRedirect) where managed models are offered, and the Overview on
// a deployment with none, which has no Catalog. It waits for the
// managed-models status; a status that cannot be read opens the Overview.
export default function CapabilitiesRedirect() {
  const status = useManagedModelsStatus();
  if (status.data?.available === true) {
    return <GatewayRedirect to={hiddenPageRedirect("capabilities")} />;
  }
  if (status.data || status.isError || (status.failureCount ?? 0) > 0) {
    return <GatewayRedirect to={paths.dashboard.gateway.overview} />;
  }
  return null;
}
