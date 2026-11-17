import { useMemo } from "react";

import { useCapabilities } from "src/hooks/useCapabilities";

import { isOmegaConsoleEnabled } from "./flags";

import {
  OMEGA_PRODUCT,
  OMEGA_PRODUCTS,
  ROLLOUT_STALE_MS,
  capabilityIdFor,
  isListed,
  partCapabilityId,
  productStatus,
} from "./rollout";

const HOSTED_PART = {
  [OMEGA_PRODUCT.SANDBOXES]: partCapabilityId("hosted_sandboxes"),
  [OMEGA_PRODUCT.DEPLOYMENTS]: partCapabilityId("hosted_deployments"),
};
const isNotAdmitted = (feature) =>
  feature?.allowed === false && feature.reason_code === "PRODUCT_NOT_ADMITTED";

/**
 * Product admission and hosted-withheld sidebar information share one cached
 * /api/capabilities/ observation. The informational rows never change
 * statusOf/listed, which still decide route access and working navigation.
 */
export function useOmegaProducts() {
  // Plain Future AGI builds (flag off) keep the capabilities query's default.
  const { data, isLoading, isError } = useCapabilities({
    staleTime: isOmegaConsoleEnabled() ? ROLLOUT_STALE_MS : Infinity,
  });
  return useMemo(() => {
    const statuses = Object.fromEntries(
      OMEGA_PRODUCTS.map((product) => [
        product,
        productStatus(data, product, { isLoading, isError }),
      ]),
    );
    // Cloud and EU share display_mode=cloud. No hostname inference, extra
    // mode query, or cached-data fallback while this observation is unreadable.
    const isHosted = !isLoading && !isError && data?.display_mode === "cloud";
    return {
      isLoading,
      isHosted,
      statusOf: (product) => statuses[product],
      listed: (product) => isListed(statuses[product]),
      hostedWithheld: (product) =>
        isHosted &&
        Boolean(HOSTED_PART[product]) &&
        isNotAdmitted(data?.features?.[HOSTED_PART[product]]) &&
        isNotAdmitted(data?.features?.[capabilityIdFor(product)]),
    };
  }, [data, isLoading, isError]);
}

export function useOmegaProductStatus(product) {
  return useOmegaProducts().statusOf(product);
}
