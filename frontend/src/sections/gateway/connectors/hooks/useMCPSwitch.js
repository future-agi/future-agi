import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { mcpSwitch } from "../mcp-turn-on";

// The organization's MCP switch for the connect forms (mcp-turn-on.js
// mcpSwitch), read while `enabled`. The drawer says when the read fails, so
// the app-wide error toast stays off it.
export function useMCPSwitch(gatewayId, { enabled = true } = {}) {
  const query = useGatewayConfig(gatewayId, {
    enabled: Boolean(gatewayId) && enabled,
    meta: { errorHandled: true },
  });
  return mcpSwitch(query);
}
