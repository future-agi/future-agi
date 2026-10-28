import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { MOCK_RUNS } from "./_fixtures/runs";
import { listRunTestExecutions, mapExecutions } from "./runs";

export const ALL_RUNS_CAP = 200;
// The executions endpoint takes any `limit`, so the list comes in a few big
// pages rather than twenty of the table's ten.
const ALL_RUNS_PAGE_SIZE = 100;
// Two big pages are heavier than the table's one, so this polls more slowly.
const ALL_RUNS_POLL_MS = 5000;

// Pages through the run-test's executions, newest first, until it has them
// all or `cap` of them. Ordinals are stamped per page exactly as the table's
// are, so "Run 7" is the same run on the chart and in the table.
export async function listAllRuns(runTestId, cap) {
  const limit = Math.min(ALL_RUNS_PAGE_SIZE, cap);
  const runs = [];
  let count = 0;
  for (let page = 1; ; page += 1) {
    // Each page needs the count the one before it reported.
    // eslint-disable-next-line no-await-in-loop
    const payload = await listRunTestExecutions(runTestId, { page, limit });
    count = payload?.count ?? 0;
    const results = payload?.results ?? [];
    runs.push(...mapExecutions(payload, (page - 1) * limit));
    if (!results.length || runs.length >= Math.min(count, cap)) break;
  }
  return { runs: runs.slice(0, cap), count };
}

// Every run of the environment up to `cap`, newest first, for views that read
// the whole history (the Runs tab's pass-rate chart). `capped` says older runs
// were left out. Same three sources as useEnvironmentRuns: the run-test's
// executions, the `?mockRuns=1` QA fixture, or the client-seeded runs.
export function useAllRuns(env, envState, { cap = ALL_RUNS_CAP } = {}) {
  const [params] = useSearchParams();
  const mockRuns = import.meta.env.DEV && params.get("mockRuns") === "1";
  const runTestId = env?.platform?.runTestId;

  const query = useQuery({
    queryKey: ["run-test-executions", runTestId, "all", cap],
    queryFn: () => listAllRuns(runTestId, cap),
    enabled: !!runTestId && !mockRuns,
    refetchInterval: (q) =>
      (q.state.data?.runs || []).some((run) => run.status === "running")
        ? ALL_RUNS_POLL_MS
        : false,
  });

  if (mockRuns) {
    return {
      runs: MOCK_RUNS.slice(0, cap),
      capped: MOCK_RUNS.length > cap,
      isLoading: false,
    };
  }
  if (runTestId) {
    return {
      runs: query.data?.runs ?? [],
      capped: (query.data?.count ?? 0) > cap,
      isLoading: query.isLoading,
    };
  }
  const localRuns = envState?.runs ?? [];
  return {
    runs: localRuns.slice(0, cap),
    capped: localRuns.length > cap,
    isLoading: false,
  };
}
