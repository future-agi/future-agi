import { useMCPStatus } from "./useMCPConfig";

// Use the existing deployment-capability source, never org-writable config.
// A failed/pending read cannot keep cached ON capabilities alive.
export function toolCallFeatures(statusQuery) {
  const ready = !statusQuery?.isError && !statusQuery?.isLoading;
  const features = statusQuery?.data?.features;
  const explorer = ready && features?.tool_call_explorer === true;
  return {
    explorer,
    download: explorer && features?.tool_call_download === true,
  };
}

export function useMCPToolCallFeatures(gatewayId) {
  const status = useMCPStatus(gatewayId);
  return toolCallFeatures(gatewayId ? status : undefined);
}
