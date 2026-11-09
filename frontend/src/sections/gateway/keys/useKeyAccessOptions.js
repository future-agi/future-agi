import { useMemo } from "react";

import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";

// What a key's allow-lists can name: the org's own providers (their ids are
// what the gateway's key ACL checks) and the models those providers serve.
// The same gateway config read the Providers page uses, so it is usually
// cached already.
export function useKeyAccessOptions(gatewayId) {
  const { data: config } = useGatewayConfig(gatewayId);
  return useMemo(() => {
    const providers = config?.providers || {};
    const names = Object.keys(providers).sort();
    const models = new Set();
    names.forEach((name) => {
      (providers[name]?.models || []).forEach((model) => {
        if (typeof model === "string" && model) models.add(model);
      });
    });
    return { providerOptions: names, modelOptions: [...models].sort() };
  }, [config]);
}
