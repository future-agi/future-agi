import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// Connecting a code agent on its own: scan a source for its agents, connect the
// one picked, and confirm what its first reading found.

export const SCAN_POLL_MS = 3000;

const unwrap = (data) => data?.result ?? data;

export const scanOpen = (scan) =>
  !scan || scan.status === "pending" || scan.status === "running";

export const mapScan = (raw) => ({
  id: raw.id,
  status: raw.status,
  kind: raw.kind,
  repository: raw.repository ?? null,
  ref: raw.ref ?? null,
  commitSha: raw.commit_sha ?? null,
  agents: (raw.agents ?? []).map((a) => ({
    name: a.name,
    subfolder: a.subfolder ?? "",
    entry: a.entry,
    framework: a.framework ?? null,
    confidence: a.confidence,
    evidence: a.evidence ?? [],
    envNames: a.env_names ?? [],
    estimates: a.estimates ?? null,
  })),
  truncated: !!raw.truncated,
  error: raw.error ?? null,
  failure: raw.failure ?? null,
});

export function useStartScan() {
  return useMutation({
    mutationFn: async (source) =>
      unwrap((await axios.post(endpoints.agentDefinitions.scan, source)).data),
  });
}

// Polls while the scan runs; stops on its result or failure.
export function useAgentScan(scanId) {
  return useQuery({
    queryKey: ["agents", "scan", scanId],
    queryFn: async () =>
      unwrap(
        (await axios.get(endpoints.agentDefinitions.scanDetail(scanId))).data,
      ),
    select: mapScan,
    enabled: !!scanId,
    refetchInterval: (query) =>
      scanOpen(query?.state?.data) ? SCAN_POLL_MS : false,
  });
}

export function useConnectAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body) =>
      unwrap((await axios.post(endpoints.agentDefinitions.connect, body)).data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["agents", "connected"] }),
  });
}

export function useFoundCheck(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        (await axios.post(endpoints.agentDefinitions.foundCheck(agentId), {}))
          .data,
      ),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["agents", "detail", agentId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["agents", "build-preflight", agentId],
        }),
      ]),
  });
}
