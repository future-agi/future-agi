import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import { TERMINAL_STATUSES } from "src/sections/common/simulation/constants/statusStyles";
import { MOCK_RUNS } from "./_fixtures/runs";

// The Runs tab's data source. For a real completed harness job the env carries
// `platform.runTestId`, so the run history is the product's real executions
// API; otherwise it is the client-seeded `envState.runs` (empty until the
// backend lands). The `?mockRuns=1` QA switch swaps in MOCK_RUNS so the
// populated state can be exercised without a completed job.

// Reads the raw executions payload ({ results, count }) for a run-test. The
// endpoint is already registered — this adds no apiPath.
export function listRunTestExecutions(runTestId) {
  return axios
    .get(endpoints.runTests.detailExecutions(runTestId))
    .then((res) => res.data);
}

// Maps one raw execution row to the workspace run shape. The camelCase field
// mapping mirrors the product's TestRunsGrid over the same `results[]`:
// `success_rate` is a 0–100 percentage; `total_chats` (chat/prompt) falls back
// to `calls_attempted` (voice). `passed`/`failed` are derived from the success
// rate since the payload carries no per-outcome counts, and there is no run
// name field, so `label` is assigned by the hook after sorting (kept out of
// this pure mapper). `executionId` mirrors `id` so a row click routes into the
// reused product execution detail.
export function executionToRun(raw) {
  const total = raw?.total_chats ?? raw?.calls_attempted ?? 0;
  const rate = raw?.success_rate ?? 0;
  const passed = Math.round((total * rate) / 100);
  const failed = Math.max(total - passed, 0);

  let status;
  if (!TERMINAL_STATUSES.includes(raw?.status)) {
    status = "running";
  } else if (raw.status === "Completed" && failed === 0) {
    status = "passed";
  } else {
    status = "failed";
  }

  return {
    id: raw?.id,
    executionId: raw?.id,
    status,
    startedAt: raw?.start_time ?? null,
    finishedAt: null,
    total,
    passed,
    failed,
    agentVersion: raw?.agent_version ?? null,
  };
}

// Maps + sorts the raw payload newest-first, then assigns ordinal labels
// (newest = highest number, matching the MOCK_RUNS convention). The `ordinal`
// is stamped alongside the label so the run-detail header can key its identity
// chip (letter + colour) off the same number the history list shows — one
// identity, assigned once at the source. Exported so `useRunDetail` reuses this
// exact derivation rather than renumbering by its own.
export function mapExecutions(payload) {
  const rows = (payload?.results ?? []).map(executionToRun);
  rows.sort(
    (a, b) => new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime(),
  );
  return rows.map((run, index) => {
    const ordinal = rows.length - index;
    return { ...run, ordinal, label: `Run ${ordinal}` };
  });
}

export function useEnvironmentRuns(env, envState) {
  const [params] = useSearchParams();
  const mockRuns = params.get("mockRuns") === "1";
  const runTestId = env?.platform?.runTestId;

  const query = useQuery({
    queryKey: ["run-test-executions", runTestId],
    queryFn: () => listRunTestExecutions(runTestId),
    enabled: !!runTestId && !mockRuns,
    select: mapExecutions,
  });

  if (mockRuns) {
    return { runs: MOCK_RUNS, isLoading: false };
  }
  if (runTestId) {
    return { runs: query.data ?? [], isLoading: query.isLoading };
  }
  return { runs: envState?.runs ?? [], isLoading: false };
}

// Where "Run simulation" / "Start simulation" navigates. A built environment
// carries the harness bridge ids, so it opens the product's own execution
// detail; otherwise it lands on the product's "Run Simulation" entry.
export function runSimulationTarget(env) {
  const runTestId = env?.platform?.runTestId;
  const testExecutionId = env?.platform?.testExecutionId;
  if (runTestId && testExecutionId) {
    return paths.dashboard.simulate.testCallDetails(runTestId, testExecutionId);
  }
  return paths.dashboard.simulate.test;
}

// Run target for a scoped run: a subset of scenarios (`ids`) and/or a repeat
// count (`trials`), from the scenario selection bar or the header run-config
// dialog. The intent rides on the URL — `?only=<id,id>` for a subset,
// `&trials=<k>` for repeats.
//
// HONEST GAP: our branch has no live-run view yet, so this appends onto the
// product run page, which does NOT read `only`/`trials` today — the run
// navigates but neither the subset nor the repeat count takes effect. Wire the
// params through once the live-run route lands (see the hosted-panel gaps note).
export function runSelectionTarget(env, ids, trials) {
  const base = runSimulationTarget(env);
  const parts = [];
  const only = (ids || []).filter(Boolean);
  // Keep the id list comma-readable (?only=a,b), encoding each id rather than
  // the separators, which URLSearchParams would percent-encode.
  if (only.length) parts.push(`only=${only.map(encodeURIComponent).join(",")}`);
  const k = Math.max(1, Math.min(20, Number(trials) || 1));
  if (k > 1) parts.push(`trials=${k}`);
  if (!parts.length) return base;
  return `${base}${base.includes("?") ? "&" : "?"}${parts.join("&")}`;
}
