import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { paramsSerializer } from "src/utils/utils";

export const CONNECTED_AGENT_FILTERS = [
  "agent_type",
  "source",
  "environments",
  "observe",
];

export const connectedAgentsQueryKey = ({
  page,
  pageSize,
  search,
  filters,
}) => ["agents", "connected", { page, pageSize, search, filters }];

// The list takes a 1-based page; the table keeps a 0-based one. Unknown
// params are a 400, so send only these.
export const buildConnectedAgentsParams = ({
  page,
  pageSize,
  search,
  filters,
}) => {
  const q = (search || "").trim();
  const params = { page: page + 1, limit: pageSize, ...(q && { search: q }) };
  CONNECTED_AGENT_FILTERS.forEach((key) => {
    if (filters?.[key]?.length) params[key] = filters[key];
  });
  return params;
};

export const mapConnectedAgent = (raw) => ({
  id: raw.id,
  name: raw.agent_name,
  description: raw.description ?? null,
  modality: raw.agent_type ?? null,
  direction:
    raw.agent_type === "voice" && typeof raw.inbound === "boolean"
      ? raw.inbound
        ? "inbound"
        : "outbound"
      : null,
  source: raw.source ?? null,
  latestLabel: raw.latest_version != null ? `v${raw.latest_version}` : null,
  versionCount: raw.version_count,
  environments: raw.environments,
  environmentCount: raw.environment_count,
  createdAt: raw.created_at ?? null,
});

// The generated client types this response as a bare array, but the server
// sends the paginated envelope, so read it here rather than through it.
const fetchConnectedAgents = async (args) => {
  const res = await axios.get(endpoints.agentDefinitions.list, {
    params: buildConnectedAgentsParams(args),
    paramsSerializer: paramsSerializer(),
  });
  return res.data;
};

export function useConnectedAgents({ page, pageSize, search, filters }) {
  return useQuery({
    queryKey: connectedAgentsQueryKey({ page, pageSize, search, filters }),
    queryFn: () => fetchConnectedAgents({ page, pageSize, search, filters }),
    select: (d) => ({
      items: (d.results ?? []).map(mapConnectedAgent),
      total: d.count ?? 0,
    }),
    placeholderData: keepPreviousData,
  });
}

export function useDeleteConnectedAgents() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentIds) =>
      axios.delete(endpoints.agentDefinitions.delete, {
        data: { agent_ids: agentIds },
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["agents", "connected"] }),
  });
}
