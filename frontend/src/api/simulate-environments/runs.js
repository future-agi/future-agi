import { useInfiniteQuery } from "@tanstack/react-query";
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

// How many executions one request asks for. RunTestExecutionsView defaults to
// 10 per page (ExtendedPageNumberPagination), so without an explicit page size
// an environment's eleventh run fell off the end of its own history.
export const RUNS_PAGE_SIZE = 25;

// Reads one page of the raw executions payload ({ results, count }) for a
// run-test. The endpoint is already registered — this adds no apiPath. `page` is
// 1-based and `limit` is the page size, the param names the view reads.
export function listRunTestExecutions(runTestId, page = 1, limit = RUNS_PAGE_SIZE) {
  return axios
    .get(endpoints.runTests.detailExecutions(runTestId), { params: { page, limit } })
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

// Flattens the loaded pages and assigns ordinal labels from the FULL history
// size, not from the rows on screen: the view orders by -created_at, so the
// first row of the first page is the newest of `count` runs and is "Run count".
// Labelling off the loaded rows renumbered the latest page as Run 1..10 every
// time a new run landed.
//
// The payload order is kept as it arrives. Re-sorting client-side on
// `start_time` (which is null for a run that has not started) would disagree
// with the server's ordering and make the count-derived ordinals wrong as soon
// as a second page is loaded.
function mapPages(data) {
  const pages = data?.pages ?? [];
  const total = pages[0]?.count ?? 0;
  const rows = pages.flatMap((page) => page?.results ?? []).map(executionToRun);
  return {
    total,
    runs: rows.map((run, index) => ({ ...run, label: `Run ${total - index}` })),
  };
}

// A page still holds a non-terminal execution, so the history has to keep
// refreshing or a running row sits at "running" forever.
const hasLiveExecution = (data) =>
  (data?.pages ?? []).some((page) =>
    (page?.results ?? []).some((raw) => !TERMINAL_STATUSES.includes(raw?.status)),
  );

const RUNS_POLL_MS = 5000;

export function useEnvironmentRuns(env, envState) {
  const [params] = useSearchParams();
  const mockRuns = params.get("mockRuns") === "1" && import.meta.env.DEV;
  const runTestId = env?.platform?.runTestId;

  const query = useInfiniteQuery({
    queryKey: ["run-test-executions", runTestId],
    queryFn: ({ pageParam }) => listRunTestExecutions(runTestId, pageParam),
    initialPageParam: 1,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce(
        (count, page) => count + (page?.results?.length ?? 0),
        0,
      );
      return loaded < (lastPage?.count ?? 0) ? allPages.length + 1 : undefined;
    },
    enabled: !!runTestId && !mockRuns,
    select: mapPages,
    refetchInterval: (q) => (hasLiveExecution(q.state.data) ? RUNS_POLL_MS : false),
  });

  if (mockRuns) {
    return {
      runs: MOCK_RUNS,
      total: MOCK_RUNS.length,
      isLoading: false,
      isError: false,
      hasMore: false,
      isFetchingMore: false,
      isPolling: false,
      fetchMore: () => {},
    };
  }
  if (runTestId) {
    return {
      runs: query.data?.runs ?? [],
      total: query.data?.total ?? 0,
      isLoading: query.isLoading,
      isError: query.isError,
      hasMore: Boolean(query.hasNextPage),
      isFetchingMore: query.isFetchingNextPage,
      isPolling: (query.data?.runs ?? []).some((run) => run.status === "running"),
      fetchMore: query.fetchNextPage,
    };
  }
  const seeded = envState?.runs ?? [];
  return {
    runs: seeded,
    total: seeded.length,
    isLoading: false,
    isError: false,
    hasMore: false,
    isFetchingMore: false,
    isPolling: false,
    fetchMore: () => {},
  };
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
