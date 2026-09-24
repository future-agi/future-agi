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
// name field, so `label` is assigned by `mapExecutions` from the server count
// and order (kept out of this pure mapper). `executionId` mirrors `id` so a row
// click routes into the
// reused product execution detail.
export function executionToRun(raw) {
  const total =
    raw?.total_calls ?? raw?.total_chats ?? raw?.calls_attempted ?? raw?.calls ?? 0;
  const hasOutcomes = raw?.outcome_passed != null;
  const rate = raw?.success_rate ?? 0;
  const passed = hasOutcomes
    ? raw.outcome_passed
    : raw?.completed_calls ?? Math.round((total * rate) / 100);
  const skipped = hasOutcomes ? raw.outcome_skipped ?? 0 : 0;
  const failed = hasOutcomes
    ? (raw.outcome_failed ?? 0) + skipped
    : raw?.failed_calls ?? Math.max(total - passed, 0);

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
    finishedAt: raw?.completed_at ?? null,
    total,
    passed,
    failed,
    pending: hasOutcomes
      ? Math.max(total - passed - failed, 0)
      : raw?.pending_calls ?? Math.max(total - passed - failed, 0),
    skipped,
    hasOutcomes,
    scenarioCount: raw?.selected_scenarios ?? null,
    scenarioIds: raw?.scenario_keys ?? [],
    trials: raw?.trials ?? 1,
    agentVersion: raw?.agent_version ?? null,
    durationS: raw?.duration ?? null,
  };
}

// Maps the raw payload and stamps each run's ordinal — the run's stable
// identity number that the run-detail header also shows (run_results_v3
// `_execution_payload`: the count of the run-test's executions created no later
// than this one). The list arrives newest-first (server `-created_at`) and
// `count` is the run-test's total, so on this page that server count is exactly
// `count - index` for row `index` — the same number the detail header reads, so
// the two never disagree, and it stays right when the list is paginated (a
// 3-row page of 12 runs is Run 12..10, not Run 3..1). The server order is
// trusted rather than re-sorted: a pending newest run has a null start_time, and
// sorting by it would drop it to the bottom and mislabel it Run 1. Exported so
// `useRunDetail` reuses this exact derivation.
export function mapExecutions(payload) {
  const results = payload?.results ?? [];
  const count = payload?.count ?? results.length;
  return results.map((raw, index) => {
    const ordinal = count - index;
    return { ...executionToRun(raw), ordinal, label: `Run ${ordinal}` };
  });
}

export function useEnvironmentRuns(env, envState) {
  const [params] = useSearchParams();
  // Dev-only QA switch — never let it populate fixture runs in a prod build.
  const mockRuns = import.meta.env.DEV && params.get("mockRuns") === "1";
  const runTestId = env?.platform?.runTestId;

  const query = useQuery({
    queryKey: ["run-test-executions", runTestId],
    queryFn: () => listRunTestExecutions(runTestId),
    enabled: !!runTestId && !mockRuns,
    select: mapExecutions,
    refetchInterval: (query) =>
      (query.state.data?.results || []).some(
        (row) => !TERMINAL_STATUSES.includes(row?.status),
      )
        ? 2000
        : false,
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

