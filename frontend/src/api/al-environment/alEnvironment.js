import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import alkAxios from "./client";

export const ALK_KEYS = {
  status: ["alk", "status"],
  sessions: ["alk", "sessions"],
  history: ["alk", "history"],
  contract: ["alk", "contract"],
  world: ["alk", "world"],
  scenarios: ["alk", "scenarios"],
  simulations: ["alk", "simulations"],
  environments: ["alk", "environments"],
  subgoals: ["alk", "subgoals"],
  runs: ["alk", "runs"],
  generation: ["alk", "generation"],
  simulation: (runId) => ["alk", "simulation", runId],
};

/**
 * The spine. Everything else on the page redraws from this, and every mutation
 * returns a fresh copy of it. Polls only while the harness reports itself busy —
 * which happens when someone drives the same server from the CLI.
 */
export const useAlkStatus = () => {
  const query = useQuery({
    queryKey: ALK_KEYS.status,
    queryFn: () => alkAxios.get("/status").then((r) => r.data),
    refetchInterval: (q) => (q.state.data?.busy ? 2000 : false),
    retry: false,
  });
  return { ...query, status: query.data ?? null };
};

/**
 * What a suite generation is doing right now.
 *
 * Polled rather than streamed, because a person may open this page halfway through a suite,
 * refresh it, or open it somewhere else, and each of those has to show the same thing. Polls
 * quickly while the fan-out is running and stops once it settles, so a finished suite is not
 * still being asked about minutes later.
 */
export const useAlkGeneration = () => {
  const query = useQuery({
    queryKey: ALK_KEYS.generation,
    queryFn: () => alkAxios.get("/generation").then((r) => r.data),
    refetchInterval: (q) => (q.state.data?.state === "running" ? 2000 : false),
    retry: false,
  });
  return { ...query, generation: query.data ?? null };
};

export const useAlkSessions = () => {
  const query = useQuery({
    queryKey: ALK_KEYS.sessions,
    queryFn: () => alkAxios.get("/sessions").then((r) => r.data),
    retry: false,
  });
  return {
    ...query,
    sessions: query.data?.sessions ?? [],
    openSessionId: query.data?.open ?? null,
  };
};

/**
 * Stored messages carry their tool calls as a `tools` array, while a live stream sends each
 * one as its own event. Flatten the stored shape into the same one-entry-per-thing sequence
 * the transcript renders, so restored history and a running turn look identical.
 */
const flattenStored = (messages = []) =>
  messages.flatMap((message) => {
    const said = message.text
      ? [{ role: message.role || "tester", text: message.text }]
      : [];
    // Keyed off what the harness actually writes to chat.jsonl — `failed`, `said` and
    // `arguments`. Read under any other names a refusal came back as a tick, and what the
    // tool was called with and what it answered were dropped, which is the whole reason
    // somebody reopens a session.
    const calls = (message.tools || []).map((one) => ({
      tool: one.label || one.name || "tool",
      target: one.target || (Array.isArray(one.said) ? one.said[0] : "") || "",
      ok: !one.failed,
      text: Array.isArray(one.said) ? one.said.join("\n") : one.said || "",
      detail: one.arguments ?? null,
    }));
    return [...said, ...calls];
  });

export const useAlkHistory = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.history,
    queryFn: () => alkAxios.get("/history").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, messages: flattenStored(query.data?.messages) };
};

export const useAlkContract = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.contract,
    queryFn: () => alkAxios.get("/contract").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, contract: query.data ?? null };
};

export const useAlkWorld = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.world,
    queryFn: () => alkAxios.get("/world").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, world: query.data ?? null };
};

/** Note: this endpoint returns a bare array, unlike every other one. */
export const useAlkScenarios = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.scenarios,
    queryFn: () => alkAxios.get("/scenarios").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, scenarios: Array.isArray(query.data) ? query.data : [] };
};

/** The simulator prompt and the sub-goal catalogue, both shown on the Environment tab. */
/**
 * Every session that has built a world, newest first. Not wired to the list yet — the
 * endpoint does not exist on the harness — but the key it caches under is already invalidated
 * by every session mutation, so swapping the fixtures for this is a one-line change.
 */
export const useAlkEnvironments = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.environments,
    queryFn: () => alkAxios.get("/environments").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, environments: query.data?.environments ?? [] };
};

export const useAlkSubgoals = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.subgoals,
    queryFn: () => alkAxios.get("/subgoals").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, subgoals: query.data ?? null };
};

/**
 * The older results format. A session whose runs predate the simulations format has nothing
 * under /api/simulations, and showing "nothing has run yet" for results that plainly exist
 * is worse than reading both.
 */
export const useAlkRuns = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.runs,
    queryFn: () => alkAxios.get("/runs").then((r) => r.data),
    enabled,
    retry: false,
  });
  // An array even before it has loaded: both readers index it, and `null` in that window
  // is a crash rather than an empty list.
  return { ...query, legacyRuns: Array.isArray(query.data) ? query.data : [] };
};

/** One file out of a scenario's folder, fetched only when the reader opens it. */
export const fetchScenarioFile = (name, path) =>
  alkAxios
    .get("/scenario-file", { params: { name, path } })
    .then((r) => r.data)
    .catch((failed) => ({
      error: failed?.response?.data?.error || failed.message,
    }));

export const useAlkSimulations = (enabled = true) => {
  const query = useQuery({
    queryKey: ALK_KEYS.simulations,
    queryFn: () => alkAxios.get("/simulations").then((r) => r.data),
    enabled,
    retry: false,
  });
  return { ...query, runs: query.data?.runs ?? [] };
};

export const useAlkSimulation = (runId) => {
  const query = useQuery({
    queryKey: ALK_KEYS.simulation(runId),
    queryFn: () => alkAxios.get(`/simulations/${runId}`).then((r) => r.data),
    enabled: Boolean(runId),
    retry: false,
  });
  return { ...query, run: query.data ?? null };
};

/**
 * Mutations all answer with a fresh status, so seed that cache from the response
 * instead of paying for a refetch, then invalidate the artifact tabs.
 */
const useAlkMutation = (mutationFn) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (data) => {
      if (data) queryClient.setQueryData(ALK_KEYS.status, data);
      [
        ALK_KEYS.sessions,
        ALK_KEYS.history,
        ALK_KEYS.contract,
        ALK_KEYS.world,
        ALK_KEYS.scenarios,
        ALK_KEYS.simulations,
        ALK_KEYS.subgoals,
        ALK_KEYS.runs,
        // Creating or deleting a session adds or removes a row in the environments list.
        ALK_KEYS.environments,
      ].forEach((queryKey) => queryClient.invalidateQueries({ queryKey }));
    },
  });
};

export const useCreateAlkSession = () =>
  useAlkMutation((agent = "") =>
    alkAxios.post("/sessions", { agent }).then((r) => r.data),
  );

export const useOpenAlkSession = () =>
  useAlkMutation((id) =>
    alkAxios.post("/sessions/open", { id }).then((r) => r.data),
  );

export const useDeleteAlkSession = () =>
  useAlkMutation((id) =>
    alkAxios.delete(`/sessions/${id}`).then((r) => r.data),
  );

export const useSetAlkStage = () =>
  useAlkMutation((stage) =>
    alkAxios.post("/stage", { stage }).then((r) => r.data),
  );
