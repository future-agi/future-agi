import React from "react";
import { useParams } from "react-router-dom";

import { paths } from "src/routes/paths";

import GatewayRedirect from "../shell/GatewayRedirect";
import AutoRoutingPage from "./auto/AutoRoutingPage";
import ModelCatalogPage from "./catalog/ModelCatalogPage";
import ModelDetailPage from "./detail/ModelDetailPage";
import { useManagedModelsStatus } from "./hooks/useManagedModels";

const AUTO_PATH = `${paths.dashboard.gateway.models}/auto`;

// The Models pages, by URL: the catalog (/models), auto routing
// (/models/auto) and a model page (/models/<provider>/<model>, the deep link
// futureagi.com/models uses). The old Catalog tab URL goes to the catalog.
//
// The catalog lists managed models, so a deployment that says it offers none
// (managed-models status available:false, every hosted organization today)
// has no catalog: its catalog URLs open Auto routing, which works with the
// organization's own providers. While the status loads, or when it cannot be
// read, the catalog page shows its own loading and Retry states.
const ModelsSection = () => {
  const { tab, provider, model } = useParams();
  const status = useManagedModelsStatus();
  if (provider && model) return <ModelDetailPage />;
  if (tab === "auto") return <AutoRoutingPage />;
  if (status.data?.available === false) {
    return <GatewayRedirect to={AUTO_PATH} />;
  }
  if (tab === "catalog") {
    return <GatewayRedirect to={paths.dashboard.gateway.models} />;
  }
  return <ModelCatalogPage />;
};

export default ModelsSection;
