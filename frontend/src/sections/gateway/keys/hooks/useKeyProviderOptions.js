import { useMemo } from "react";

import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useOrgConfig } from "../../providers/hooks/useOrgConfig";
import {
  useManagedCatalog,
  useManagedModelsStatus,
} from "../../models/hooks/useManagedModels";
import { keyProviderOptions } from "../keyProviderOptions";

// The "Allowed providers" options for a key (keyProviderOptions): the org's
// providers, as the Providers page lists them, and the Future AGI provider
// when the organization has enabled Future AGI models.
export function useKeyProviderOptions(gatewayId) {
  const { data: config } = useGatewayConfig(gatewayId);
  const { data: orgConfig } = useOrgConfig();
  const status = useManagedModelsStatus();
  const futureAgiOffered =
    status.data?.available === true && status.data?.enabled === true;
  const catalog = useManagedCatalog({ enabled: futureAgiOffered });
  const providers = config?.providers || orgConfig?.providers;
  return useMemo(
    () =>
      keyProviderOptions({
        providers,
        futureAgiOffered,
        catalog: catalog.data,
      }),
    [providers, futureAgiOffered, catalog.data],
  );
}
