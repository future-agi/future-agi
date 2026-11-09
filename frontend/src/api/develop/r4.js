// Round 4 hooks (lane F; wire shapes in src/_mock/prototype/CONTRACT.md
// "Round 4" and fixtures/world/r4Shapes.js). Frozen API: the hooks and the
// query keys below. Bodies are plain axios calls; every key starts with
// "r4". Polling hooks take `{poll}` (ms; 0/undefined = no polling).
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const E = () => endpoints.develop.r4;
const result = (res) => res?.data?.result ?? res?.data;
const csv = (ids) => (ids ?? []).filter(Boolean).join(",");

// ------------------------------------------------------------------ keys

export const r4Keys = Object.freeze({
  catalog: () => ["r4", "catalog"],
  call: (callId) => ["r4", "call", callId],
  agentDefinitions: (params) => ["r4", "agent-definitions", params ?? {}],
  machines: () => ["r4", "connect", "machines"],
  session: (token) => ["r4", "connect", "session", token],
  activity: (runId) => ["r4", "run", runId, "activity"],
  paths: (runId, compare) => ["r4", "run", runId, "paths", compare ?? null],
  voiceSummary: (datasetId, params) => [
    "r4",
    "voice-summary",
    datasetId,
    params ?? {},
  ],
});

const pollOf = (poll) => (poll && poll > 0 ? poll : false);

// --------------------------------------------------------------- reads

/** GET the run-source catalog (cards, entries, counts, models). */
export function useRunSourceCatalog(opts = {}) {
  return useQuery({
    queryKey: r4Keys.catalog(),
    queryFn: () => axios.get(E().catalog),
    select: result,
    staleTime: 5 * 60_000,
    ...opts,
  });
}

/** GET one voice call (CallValue). */
export function useCall(callId, opts = {}) {
  const { enabled = true, ...rest } = opts;
  return useQuery({
    queryKey: r4Keys.call(callId),
    queryFn: () => axios.get(E().call(callId)),
    select: result,
    enabled: Boolean(callId) && enabled,
    staleTime: 60_000,
    ...rest,
  });
}

/** GET Simulate agent definitions (the existing list endpoint). */
export function useAgentDefinitions(params = {}, opts = {}) {
  return useQuery({
    queryKey: r4Keys.agentDefinitions(params),
    queryFn: () => axios.get(endpoints.agentDefinitions.list, { params }),
    select: (res) => res?.data,
    staleTime: 60_000,
    ...opts,
  });
}

/** GET connected machines (C). `{poll}` refreshes heartbeats. */
export function useConnectMachines({ poll, ...opts } = {}) {
  return useQuery({
    queryKey: r4Keys.machines(),
    queryFn: () => axios.get(E().connectMachines),
    select: result,
    refetchInterval: pollOf(poll),
    retry: false,
    ...opts,
  });
}

/** GET a live run's activity (C). `{poll}` while running. */
export function useRunActivity(runId, { poll, ...opts } = {}) {
  const { enabled = true, ...rest } = opts;
  return useQuery({
    queryKey: r4Keys.activity(runId),
    queryFn: () => axios.get(E().runActivity(runId)),
    select: result,
    enabled: Boolean(runId) && enabled,
    refetchInterval: pollOf(poll),
    retry: false,
    ...rest,
  });
}

/** GET a run's aggregate paths (T); `compare` adds the second run. */
export function useRunPaths(runId, { compare, ...opts } = {}) {
  const { enabled = true, ...rest } = opts;
  return useQuery({
    queryKey: r4Keys.paths(runId, compare),
    queryFn: () =>
      axios.get(E().runPaths(runId), {
        params: compare ? { compare } : undefined,
      }),
    select: result,
    enabled: Boolean(runId) && enabled,
    placeholderData: keepPreviousData,
    ...rest,
  });
}

/** GET a dataset's voice summary (X). */
export function useVoiceSummary(
  datasetId,
  { runIds, baselineId, ...opts } = {},
) {
  const params = { runs: csv(runIds), baseline: baselineId ?? undefined };
  const { enabled = true, ...rest } = opts;
  return useQuery({
    queryKey: r4Keys.voiceSummary(datasetId, params),
    queryFn: () => axios.get(E().voiceSummary(datasetId), { params }),
    select: result,
    enabled: Boolean(datasetId) && enabled,
    placeholderData: keepPreviousData,
    ...rest,
  });
}

// --------------------------------------------------------------- writes

const post = (url, body) => axios.post(url, body ?? {}).then(result);

/** POST a run control action (stop, resume, watch_on, …). */
export function useRunControl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, action }) => post(E().runControl(runId), { action }),
    // L2 G3 (one clock): Stop / Resume / confirm act on the run itself, so
    // the header, the grid and the tray refetch with the strip.
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** POST a simulated upload `{rows: 1|"all"}` (prototype). */
export function useSimulateUpload() {
  return useMutation({
    mutationFn: ({ runId, rows }) => post(E().simulateUpload(runId), { rows }),
  });
}

/** POST rerun with changes `{params, recipe?, scope, row_ids, trials}`. */
export function useRerunRun() {
  return useMutation({
    mutationFn: ({ runId, ...body }) => post(E().rerun(runId), body),
  });
}
