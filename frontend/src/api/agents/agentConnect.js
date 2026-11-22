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

// Polls while the scan runs; stops on its result, its failure, or a request
// that errored, so a 404 or 500 is not asked again every few seconds.
export const scanPollInterval = (query) =>
  query?.state?.status !== "error" && scanOpen(query?.state?.data)
    ? SCAN_POLL_MS
    : false;

export function useAgentScan(scanId) {
  return useQuery({
    queryKey: ["agents", "scan", scanId],
    queryFn: async () =>
      unwrap(
        (await axios.get(endpoints.agentDefinitions.scanDetail(scanId))).data,
      ),
    select: mapScan,
    enabled: !!scanId,
    refetchInterval: scanPollInterval,
  });
}

export function useConnectAvailability() {
  return useQuery({
    queryKey: ["agents", "connect", "availability"],
    queryFn: async () =>
      unwrap(
        (await axios.get(endpoints.agentDefinitions.connectAvailability)).data,
      ),
    select: (raw) => ({ readingEnabled: raw?.reading_enabled === true }),
    staleTime: Infinity,
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
