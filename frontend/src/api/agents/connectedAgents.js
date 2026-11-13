import { keepPreviousData, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const connectedAgentsQueryKey = ({ page, pageSize, search }) => [
  "agents",
  "connected",
  { page, pageSize, search },
];

// The list takes a 1-based page; the table keeps a 0-based one. Unknown
// params are a 400, so send only these.
export const buildConnectedAgentsParams = ({ page, pageSize, search }) => {
  const q = (search || "").trim();
  return { page: page + 1, limit: pageSize, ...(q && { search: q }) };
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
  });
  return res.data;
};

export function useConnectedAgents({ page, pageSize, search }) {
  return useQuery({
    queryKey: connectedAgentsQueryKey({ page, pageSize, search }),
    queryFn: () => fetchConnectedAgents({ page, pageSize, search }),
    select: (d) => ({
      items: (d.results ?? []).map(mapConnectedAgent),
      total: d.count ?? 0,
    }),
    placeholderData: keepPreviousData,
  });
}
