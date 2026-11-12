import React from "react";
import { useParams } from "react-router-dom";

import { paths } from "src/routes/paths";

import GatewayRedirect from "../shell/GatewayRedirect";
import AutoRoutingPage from "./auto/AutoRoutingPage";
import ModelCatalogPage from "./catalog/ModelCatalogPage";
import OrgModelsPage from "./catalog/OrgModelsPage";
import ModelDetailPage from "./detail/ModelDetailPage";
import { useManagedModelsStatus } from "./hooks/useManagedModels";

// /models lists the managed catalog when offered, or the organization's
// configured models when the deployment explicitly reports available:false.
// /models/your always exposes the own-provider index, including when the
// managed catalog is available. Auto routing and detail links stay independent.
// Unknown/failed managed status retains the catalog's loading/Retry states;
// it is not evidence that the deployment offers no managed models.
const ModelsSection = () => {
  const { tab, provider, model } = useParams();
  const status = useManagedModelsStatus();
  if (provider && model) return <ModelDetailPage />;
  if (tab === "auto") return <AutoRoutingPage />;
  if (tab === "catalog") {
    return <GatewayRedirect to={paths.dashboard.gateway.models} />;
  }
  if (tab === "your" || status.data?.available === false) {
    return <OrgModelsPage catalogOffered={status.data?.available === true} />;
  }
  return <ModelCatalogPage />;
};

export default ModelsSection;
