import { useMemo } from "react";

import { useApiKeysStatus } from "src/api/model/api-keys";

import { logoMap } from "../vendorLogos";

// Future AGI's provider logos (the AI Providers page's list, shared query).
// A failed read leaves every vendor on its initial; nothing else depends on
// it, so it raises no app-wide error.
export function useVendorLogos() {
  const { data } = useApiKeysStatus({ meta: { errorHandled: true } });
  return useMemo(() => logoMap(data), [data]);
}
