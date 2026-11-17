import { useQuery } from "@tanstack/react-query";
import { apiPath } from "src/api/contracts/api-surface";
import axios, { endpoints } from "src/utils/axios";
import {
  refreshGatewayConfig,
  useGatewayConfigWrite,
} from "../../providers/hooks/useGatewayConfig";

const QUERY_KEY = "agentcc-budget-cap-editor";

// A gateway ID can be shared by organizations. Never reuse a cap read across
// organizations, and bind each HTTP request to the organization shown in the editor.
export function useBudgetCapEditor({ link, orgId, gatewayId }) {
  const workspace = link.mode === "workspace";
  const available = Boolean(orgId && (workspace || gatewayId));
  const url = workspace
    ? apiPath(
        "/agentcc/mcp/service/workspaces/{workspace_id}/agent-key-policy/",
        {
          workspace_id: link.id,
        },
      )
    : available
      ? endpoints.gateway.config(gatewayId)
      : null;
  const requestConfig = {
    timeout: 30000,
    headers: { "X-Organization-Id": orgId },
  };
  const query = useQuery({
    queryKey: [QUERY_KEY, orgId, gatewayId, link.level, link.id],
    queryFn: async () => {
      const { data } = await axios.get(url, requestConfig);
      if (!data?.result) throw new Error("The saved cap could not be read.");
      return data.result;
    },
    enabled: available,
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    retry: false,
    meta: { errorHandled: true },
  });
  const mutation = useGatewayConfigWrite(
    async (body) => {
      if (!available)
        throw new Error("Select an organization and gateway before saving.");
      const { data } = workspace
        ? await axios.put(url, body, requestConfig)
        : await axios.post(
            endpoints.gateway.updateConfig(gatewayId),
            body,
            requestConfig,
          );
      if (!data?.result)
        throw new Error(
          "The cap save could not be confirmed. Reload before retrying.",
        );
      return data.result;
    },
    (client) =>
      Promise.all([
        client.invalidateQueries({ queryKey: [QUERY_KEY, orgId] }),
        refreshGatewayConfig(client, ["agentcc-org-config"]),
      ]),
  );
  return { query, mutation };
}
