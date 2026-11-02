import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  getMCPServerId,
  getMCPServerStatusLabel,
} from "src/sections/gateway/mcp/utils";

const hostOf = (url) => {
  try {
    return new URL(url).host;
  } catch {
    return url || "";
  }
};

// The MCP servers registered in the workspace's Gateway and the tools each
// lists, read the way Gateway → MCP reads them: the single gateway, its
// config.mcp.servers, mcp-status and the mcp-tools catalogue. Fetched only
// while the add-tools drawer is open.
export default function useGatewayMcp(enabled) {
  const gateways = useQuery({
    queryKey: ["gateway-list"],
    queryFn: async () => (await axios.get(endpoints.gateway.list)).data.result,
    enabled,
  });
  const gatewayId = gateways.data?.[0]?.id || null;
  const on = enabled && !!gatewayId;
  const config = useQuery({
    queryKey: ["gateway-config", gatewayId],
    queryFn: async () => (await axios.get(endpoints.gateway.config(gatewayId))).data.result,
    enabled: on,
  });
  const status = useQuery({
    queryKey: ["mcp-status", gatewayId],
    queryFn: async () => (await axios.get(endpoints.gateway.mcpStatus(gatewayId))).data.result,
    enabled: on,
  });
  const tools = useQuery({
    queryKey: ["mcp-tools", gatewayId],
    queryFn: async () => (await axios.get(endpoints.gateway.mcpTools(gatewayId))).data.result,
    enabled: on,
  });

  const health = {};
  for (const s of status.data?.servers || []) health[getMCPServerId(s)] = s;
  const catalogue = Array.isArray(tools.data) ? tools.data : [];
  const toolsOf = (id) =>
    catalogue
      .filter((t) => t.server === id)
      .map((t) => ({
        name: t.name,
        desc: t.description || "",
        readOnly: !!t.annotations?.readOnlyHint,
        destructive: !!t.annotations?.destructiveHint,
        openWorld: !!t.annotations?.openWorldHint,
        deprecated: t.deprecated ? t.replaced_by || "another tool" : null,
      }));
  const servers = Object.entries(config.data?.mcp?.servers || {}).map(([id, cfg]) => {
    const h = health[id];
    const label = getMCPServerStatusLabel(h);
    const connected = !h || h.healthy === true || /connected|healthy/i.test(label);
    return {
      id,
      label: id,
      host: hostOf(cfg?.url),
      status: connected ? "connected" : "error",
      error: connected ? null : label,
      toolCount: toolsOf(id).length,
    };
  });

  return {
    servers,
    toolsOf,
    isLoading: gateways.isLoading || config.isLoading || status.isLoading,
  };
}
