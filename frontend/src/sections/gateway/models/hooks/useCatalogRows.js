import { useMemo } from "react";

import { useGatewayContext } from "../../context/useGatewayContext";
import { useProviderHealth } from "../../providers/hooks/useGatewayConfig";
import { buildCatalogRows } from "../catalogRows";
import { useManagedCatalog, useManagedModelsStatus } from "./useManagedModels";
import { useVendorLogos } from "./useVendorLogos";

// Everything the Models pages read: managed status, the managed catalog (only
// where the deployment offers one), the org's own providers, and the catalog
// as rows with this org's access and prices.
export function useCatalogRows() {
  const { gateway, gatewayId } = useGatewayContext();
  const status = useManagedModelsStatus();
  const offered = status.data?.available !== false;
  const catalog = useManagedCatalog({
    enabled: Boolean(status.data) && offered,
  });
  const providers = useProviderHealth(gatewayId);
  const logos = useVendorLogos();

  const providerList = providers.data?.providers;
  const rows = useMemo(
    () =>
      catalog.data
        ? buildCatalogRows(catalog.data.models, {
            providers: Array.isArray(providerList) ? providerList : [],
            managedStatus: status.data,
            logos,
          })
        : undefined,
    [catalog.data, providerList, status.data, logos],
  );

  return {
    gateway,
    gatewayId,
    baseUrl: gateway?.baseUrl,
    status,
    catalog,
    providers,
    rows,
    offered,
    markupPercent: catalog.data?.markup_percent ?? status.data?.markup_percent,
    isLoading: status.isLoading || (offered && catalog.isLoading),
  };
}
