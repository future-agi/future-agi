import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

import { fixtureFixRun, fixtureFixRuns } from "./_fixtures/fixRuns";

// Fix my agent v0 (agent-fix-runs). Until the backend lands every read and
// write answers from contract-shaped fixtures; flip this to "api" to go live.
export const FIX_RUNS_SOURCE = "fixtures";

export const FIX_LIVE_STATES = new Set(["queued", "running", "stopping"]);
export const FIX_POLL_MS = 5000;

export const fixRunsKey = (envId) => ["agent-fix-runs", envId];
export const fixRunKey = (fixId) => ["agent-fix-run", fixId];

export const fixPollInterval = (fix) =>
  fix && FIX_LIVE_STATES.has(fix.state) ? FIX_POLL_MS : false;

const fromFixtures = () => FIX_RUNS_SOURCE === "fixtures";

export async function listFixRuns(envId) {
  if (fromFixtures()) return fixtureFixRuns(envId);
  const res = await axios.get(endpoints.agentFixRuns.list(), {
    params: { environment_id: envId },
  });
  return Array.isArray(res.data) ? res.data : res.data?.results ?? [];
}

export async function getFixRun(fixId) {
  if (fromFixtures()) {
    const fix = fixtureFixRun(fixId);
    if (!fix) throw new Error("not_found");
    return fix;
  }
  const res = await axios.get(endpoints.agentFixRuns.detail(fixId));
  return res.data;
}

export function useEnvironmentFixRuns(envId) {
  const { data, isLoading } = useQuery({
    queryKey: fixRunsKey(envId),
    queryFn: () => listFixRuns(envId),
    enabled: !!envId,
    refetchInterval: (query) =>
      (query.state.data || []).some((f) => FIX_LIVE_STATES.has(f.state))
        ? FIX_POLL_MS
        : false,
  });
  const fixes = data ?? [];
  return { fixes, count: fixes.length, isLoading };
}

export function useFixRun(fixId) {
  const { data, isLoading, error } = useQuery({
    queryKey: fixRunKey(fixId),
    queryFn: () => getFixRun(fixId),
    enabled: !!fixId,
    refetchInterval: (query) => fixPollInterval(query.state.data),
  });
  return { fix: data, isLoading, error };
}
