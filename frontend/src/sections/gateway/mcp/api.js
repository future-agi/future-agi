// The MCP console's tool-call log, credential vault and probe paths, built by
// the endpoint registry from the generated API contract
// (agentcc/urls_mcp_toolcalls.py and urls_mcp_credentials.py serve them).
import { endpoints } from "src/utils/axios";

const gw = endpoints.gateway;

export const MCP_API = Object.freeze({
  toolCalls: gw.mcpToolCalls,
  toolCallsExport: gw.mcpToolCallsExport,
  siemDestinations: gw.siemDestinations,
  siemTestConnection: gw.siemTestConnection,
  siemDestination: (id) => gw.siemDestination(id),
  siemDeliveries: gw.siemDeliveries,
  siemRetryDelivery: (id) => gw.siemRetryDelivery(id),
  siemDiscardDelivery: (id) => gw.siemDiscardDelivery(id),
  toolCall: (id) => gw.mcpToolCall(id),
  credentials: gw.mcpCredentials,
  credential: (id) => gw.mcpCredential(id),
  rotateCredential: (id) => gw.mcpRotateCredential(id),
  probe: gw.mcpProbe,
});

// The message a failed request should show: the API's own, else axios'.
export function apiErrorMessage(error, fallback) {
  const data = error?.response?.data;
  const fromApi =
    (typeof data?.result === "string" && data.result) ||
    (typeof data?.message === "string" && data.message) ||
    (typeof data?.detail === "string" && data.detail) ||
    (typeof data?.error === "string" && data.error);
  if (fromApi) return fromApi;
  if (error?.response?.status === 403) {
    return "Only an organization owner or admin can change this.";
  }
  return error?.message || fallback;
}
