import { act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

// The real run grid, its status tracking, the real useKpis and the real
// QueryClient, inside the shared theme and router. Only the network, the route
// params and AG Grid's server-side row model are fakes.

type RunStatus = "running" | "completed" | "failed";
type CallRow = { id: string; status: string };
type RunPage = {
  status?: RunStatus;
  count: number;
  results: CallRow[];
  column_order: never[];
};
type Kpis = {
  agent_type: "voice";
  total_calls: number;
  connected_calls: number;
};
type Decision = { disabled: boolean; reason: string };
type RowsUpdate = { rowData?: CallRow[]; update?: CallRow[] };
type GridApi = {
  setGridOption: () => void;
  showNoRowsOverlay: () => void;
  hideOverlay: () => void;
  applyServerSideTransaction: (transaction: RowsUpdate) => void;
  getCacheBlockState: () => Record<number, object>;
  refreshServerSide: () => void;
};
type GetRowsParams = {
  request: { startRow: number; endRow: number };
  api: GridApi;
  success: (result: RowsUpdate) => void;
  fail: () => void;
};
type Datasource = { getRows: (params: GetRowsParams) => Promise<void> };
type GridProps = { serverSideDatasource: Datasource };

const { server, grid, route } = vi.hoisted(() => {
  const blocks = { count: 1 };
  const mounted: { datasource: Datasource | null } = { datasource: null };
  // The rows the grid shows, by call id: a block's read or a poll's update.
  const rows = new Map<string, CallRow>();
  const showRows = ({ rowData, update }: RowsUpdate) => {
    for (const row of [...(rowData ?? []), ...(update ?? [])]) {
      rows.set(row.id, row);
    }
  };
  const api: GridApi = {
    setGridOption: () => {},
    showNoRowsOverlay: () => {},
    hideOverlay: () => {},
    applyServerSideTransaction: showRows,
    getCacheBlockState: () =>
      Object.fromEntries(
        Array.from({ length: blocks.count }, (_, block) => [block, {}]),
      ),
    // AG Grid reads every cached block, and always the first one: a run with
    // no call rows yet still has its first page read, but no cached block.
    refreshServerSide: () => {
      for (let block = 0; block < Math.max(blocks.count, 1); block++) {
        void mounted.datasource?.getRows({
          request: { startRow: block * 30, endRow: (block + 1) * 30 },
          api,
          success: showRows,
          fail: () => {},
        });
      }
    },
  };
  return {
    // GET /simulate/test-executions/<id>/?page=<n> and GET .../<id>/kpis/.
    server: {
      runPage: vi.fn<(url: string, page: number) => Promise<RunPage>>(),
      kpis: vi.fn<(url: string) => Promise<Kpis>>(),
    },
    grid: { blocks, mounted, api, rows },
    // The :executionId the run page is on.
    route: { executionId: "e1" },
  };
});

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  default: {
    get: (url: string, config?: { params?: { page: number } }) =>
      (url.endsWith("/kpis/")
        ? server.kpis(url)
        : server.runPage(url, config?.params?.page ?? 1)
      ).then((data) => ({ data })),
  },
}));

// AG Grid's server-side row model asks for every cached block at once when it
// mounts, so a return visit with five cached pages reads all five together.
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useEffect, useImperativeHandle } = await import("react");
  const AgGridReact = forwardRef<{ api: GridApi }, GridProps>(
    function FakeServerSideGrid({ serverSideDatasource }, ref) {
      useImperativeHandle(ref, () => ({ api: grid.api }), []);
      useEffect(() => {
        grid.mounted.datasource = serverSideDatasource;
        grid.api.refreshServerSide();
      }, [serverSideDatasource]);
      return null;
    },
  );
  return { AgGridReact };
});
vi.mock("src/utils/ag-grid-setup", () => ({}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => [null, () => {}],
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useParams: () => ({ executionId: route.executionId }),
}));
vi.mock("../TestDetailDrawer/TestDetailSideDrawer", () => ({
  default: () => null,
}));
vi.mock("../TestDetailConfigureEval", () => ({ default: () => null }));

const { kpisQueryOptions } = await import("src/hooks/useKpis");
const { useFixMyAgentBlocked } = await import("../common");
const { resetState, useTestExecutionStore } = await import("../states");
const { default: TestRunDetailGrid } = await import("../TestRunDetailGrid");

const EXECUTION_ID = "e1";
const POLL_MS = 5000;
const ENABLED: Decision = { disabled: false, reason: "" };
const BLOCKED: Decision = {
  disabled: true,
  reason: "You need at least 15 connected calls to debug failures.",
};
const COUNT_NOT_LOADED: Decision = {
  disabled: true,
  reason: "Couldn't load the call count. Refresh to try again.",
};
const STILL_RUNNING: Decision = {
  disabled: true,
  reason:
    "The test run is still running. You can debug its failures once it finishes.",
};

const runPage = (status: RunStatus, results: CallRow[] = []): RunPage => ({
  status,
  count: results.length,
  results,
  column_order: [],
});
const kpis = (connected_calls: number): Kpis => ({
  agent_type: "voice",
  total_calls: 20,
  connected_calls,
});
const deferred = <T,>() => {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

type RunPageOptions = { cachedConnectedCalls?: number; cachedPages?: number };

// Mounts the run page's grid beside Debug failures. A cached count is the
// last KPI poll from an earlier visit or from while the run was in progress.
const openRunPage = ({
  cachedConnectedCalls,
  cachedPages = 1,
}: RunPageOptions = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (cachedConnectedCalls !== undefined) {
    queryClient.setQueryData(
      kpisQueryOptions(EXECUTION_ID).queryKey,
      kpis(cachedConnectedCalls),
    );
  }
  grid.blocks.count = cachedPages;
  const decision: { current: Decision | null } = { current: null };
  const DebugFailures = () => {
    decision.current = useFixMyAgentBlocked(route.executionId);
    return null;
  };
  // The grid mounts only on the Call Details tab; Debug failures sits in the
  // tab bar, above every tab.
  const page = (onCallDetails = true) => (
    <QueryClientProvider client={queryClient}>
      {onCallDetails && <TestRunDetailGrid />}
      <DebugFailures />
    </QueryClientProvider>
  );
  const { rerender } = render(page());
  // The user opens another execution of the run on the same page.
  const openExecution = (executionId: string) =>
    act(async () => {
      route.executionId = executionId;
      rerender(page());
    });
  // The user opens another tab of the run, such as Analytics.
  const leaveCallDetails = () =>
    act(async () => {
      rerender(page(false));
    });
  return { decision, queryClient, openExecution, leaveCallDetails };
};

// AG Grid re-reads its cached blocks, as after a rerun. Production's refresh
// path is outside this test; only the grid's reads are driven here.
const reloadFakeGridBlocks = () =>
  act(async () => {
    grid.api.refreshServerSide();
  });

const poll = () =>
  act(async () => {
    vi.advanceTimersByTime(POLL_MS);
  });

// Lets reads that already answered finish their handling.
const settle = () =>
  act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

beforeEach(() => {
  // Only the grid's 5 s poll is faked; queries and waits run on real timers.
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  server.runPage.mockReset();
  server.kpis.mockReset();
  grid.rows.clear();
  route.executionId = EXECUTION_ID;
  resetState();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Debug failures once the grid sees the run finish", () => {
  it.each<[string, RunStatus, number, number, Decision]>([
    [
      "opens when the final count reaches 15 after a last poll of 14",
      "completed",
      14,
      15,
      ENABLED,
    ],
    [
      "enables after completion when both the cached and final counts are 15",
      "completed",
      15,
      15,
      ENABLED,
    ],
    [
      "stays blocked when the final count is still 14",
      "completed",
      14,
      14,
      BLOCKED,
    ],
    [
      "blocks a failed run whose final count drops to 14",
      "failed",
      15,
      14,
      BLOCKED,
    ],
  ])("%s", async (_name, finished, cached, final, expected) => {
    server.runPage.mockResolvedValue(runPage("running"));
    const { decision } = openRunPage({ cachedConnectedCalls: cached });
    await vi.waitFor(() => expect(decision.current).toEqual(STILL_RUNNING));
    expect(server.kpis).not.toHaveBeenCalled();

    server.runPage.mockResolvedValue(runPage(finished));
    server.kpis.mockResolvedValue(kpis(final));
    await poll();

    await vi.waitFor(() => expect(decision.current).toEqual(expected));
    expect(server.kpis).toHaveBeenCalledTimes(1);
  });

  it("decides on a read made after completion when the first read is still in flight", async () => {
    const firstRead = deferred<Kpis>();
    server.kpis.mockReturnValueOnce(firstRead.promise);
    server.runPage.mockResolvedValue(runPage("running"));
    const { decision } = openRunPage();
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("running"),
    );
    expect(server.kpis).toHaveBeenCalledTimes(1);

    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(15));
    await poll();
    await vi.waitFor(() => expect(server.kpis).toHaveBeenCalledTimes(2));

    // The first read answers last, with what it saw before the run finished.
    await act(async () => firstRead.resolve(kpis(14)));
    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
  });
});

describe("Debug failures on a return visit to a finished run", () => {
  it("reads the final count once, though five cached pages each see the run finished", async () => {
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(15));
    const { decision } = openRunPage({
      cachedConnectedCalls: 14,
      cachedPages: 5,
    });

    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
    expect(server.runPage).toHaveBeenCalledTimes(5);
    expect(server.kpis).toHaveBeenCalledTimes(1);

    // A finished run is not polled.
    await poll();
    expect(server.runPage).toHaveBeenCalledTimes(5);
    expect(server.kpis).toHaveBeenCalledTimes(1);
  });
});

// The grid's first status is already "completed", and the page's first KPI
// read is still in flight. That read may have been sampled before
// the run finished, so its 14 must not decide a run that finished at 15.
describe("Debug failures when the first status read is already finished", () => {
  it("opens on a fresh first read of 15 (control)", async () => {
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(15));
    const { decision, queryClient } = openRunPage();

    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
    expect(
      queryClient.getQueryData(kpisQueryOptions(EXECUTION_ID).queryKey),
    ).toEqual(kpis(15));
  });

  it("reads again after a delayed first read answers 14 for a run that finished at 15", async () => {
    const firstRead = deferred<Kpis>();
    server.kpis.mockReturnValueOnce(firstRead.promise);
    server.kpis.mockResolvedValue(kpis(15));
    server.runPage.mockResolvedValue(runPage("completed"));
    const { decision, queryClient } = openRunPage();
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("completed"),
    );

    await act(async () => firstRead.resolve(kpis(14)));
    // Three poll intervals change nothing.
    await poll();
    await poll();
    await poll();

    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
    expect(
      queryClient.getQueryData(kpisQueryOptions(EXECUTION_ID).queryKey),
    ).toEqual(kpis(15));
    expect(server.kpis).toHaveBeenCalledTimes(2);
  });

  it("keeps checking while the final read is in flight, then blocks a run that finished at 14", async () => {
    const finalRead = deferred<Kpis>();
    server.kpis.mockResolvedValueOnce(kpis(15));
    server.kpis.mockReturnValueOnce(finalRead.promise);
    server.runPage.mockResolvedValue(runPage("completed"));
    const { decision } = openRunPage();
    await vi.waitFor(() => expect(server.kpis).toHaveBeenCalledTimes(2));
    await settle();
    // The first read answered 15, but it says nothing about the finished run.
    expect(decision.current).toEqual({ disabled: true, reason: "" });

    await act(async () => finalRead.resolve(kpis(14)));
    await vi.waitFor(() => expect(decision.current).toEqual(BLOCKED));
  });
});

describe("Debug failures when the user leaves Call Details", () => {
  it("decides once the final read lands, though the grid is gone", async () => {
    const finalRead = deferred<Kpis>();
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockReturnValue(finalRead.promise);
    const { decision, leaveCallDetails } = openRunPage({
      cachedConnectedCalls: 14,
    });
    await vi.waitFor(() => expect(server.kpis).toHaveBeenCalledTimes(1));

    await leaveCallDetails();
    await act(async () => finalRead.resolve(kpis(15)));
    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
  });
});

describe("Debug failures when the final KPI read fails", () => {
  // React Query waits a second before its one retry.
  const RETRY_WAIT = { timeout: 3000 };

  it("decides on the retry when the first try fails", async () => {
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockRejectedValueOnce(new Error("network"));
    server.kpis.mockResolvedValue(kpis(15));
    const { decision } = openRunPage({ cachedConnectedCalls: 14 });

    await vi.waitFor(
      () => expect(decision.current).toEqual(ENABLED),
      RETRY_WAIT,
    );
    expect(server.kpis).toHaveBeenCalledTimes(2);
  });

  it("says why when the retry fails too, and decides on a later read", async () => {
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockRejectedValue(new Error("network"));
    const { decision, queryClient } = openRunPage({ cachedConnectedCalls: 14 });

    await vi.waitFor(
      () => expect(decision.current).toEqual(COUNT_NOT_LOADED),
      RETRY_WAIT,
    );
    expect(server.kpis).toHaveBeenCalledTimes(2);

    // Any later read, such as a refetch when the window regains focus.
    server.kpis.mockResolvedValue(kpis(15));
    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: kpisQueryOptions(EXECUTION_ID).queryKey,
      });
    });
    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
  });
});

// A read the request rejects, and an answer that carries no status.
const failedReads: [string, () => void][] = [
  ["is rejected", () => server.runPage.mockRejectedValue(new Error("network"))],
  [
    "answers without a status",
    () =>
      server.runPage.mockResolvedValue({
        ...runPage("running"),
        status: undefined,
      }),
  ],
];

describe("Debug failures when a status read fails", () => {
  it.each(failedReads)(
    "keeps polling and keeps the cached count when the read %s",
    async (_name, failTheRead) => {
      server.runPage.mockResolvedValue(runPage("running"));
      const { decision } = openRunPage({ cachedConnectedCalls: 14 });
      await vi.waitFor(() => expect(decision.current).toEqual(STILL_RUNNING));

      failTheRead();
      await poll();
      await vi.waitFor(() => expect(server.runPage).toHaveBeenCalledTimes(2));
      await settle();
      expect(decision.current).toEqual(STILL_RUNNING);
      expect(useTestExecutionStore.getState().status).toBe("running");
      expect(server.kpis).not.toHaveBeenCalled();

      server.runPage.mockResolvedValue(runPage("completed"));
      server.kpis.mockResolvedValue(kpis(15));
      await poll();
      await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
      expect(server.runPage).toHaveBeenCalledTimes(3);
      expect(server.kpis).toHaveBeenCalledTimes(1);
    },
  );
});

describe("Debug failures after a rerun", () => {
  it("reads the final count again when a rerun of the finished run finishes", async () => {
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(14));
    const { decision } = openRunPage({ cachedConnectedCalls: 14 });
    await vi.waitFor(() => expect(server.kpis).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(decision.current).toEqual(BLOCKED));

    server.runPage.mockResolvedValue(runPage("running"));
    await reloadFakeGridBlocks();
    await vi.waitFor(() => expect(decision.current).toEqual(STILL_RUNNING));

    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(15));
    await poll();
    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
    expect(server.kpis).toHaveBeenCalledTimes(2);
  });
});

describe("Debug failures on a run with no calls yet", () => {
  it("keeps reading the status until the run finishes", async () => {
    server.runPage.mockResolvedValue(runPage("running"));
    const { decision } = openRunPage({
      cachedConnectedCalls: 0,
      cachedPages: 0,
    });
    await vi.waitFor(() => expect(decision.current).toEqual(STILL_RUNNING));

    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(0));
    await poll();
    await vi.waitFor(() => expect(decision.current).toEqual(BLOCKED));
    expect(server.runPage).toHaveBeenCalledTimes(2);
    expect(server.kpis).toHaveBeenCalledTimes(1);

    // A finished run is not polled.
    await poll();
    expect(server.runPage).toHaveBeenCalledTimes(2);
  });
});

// The user opens execution B while a page of execution A is still being read. B starts polling; then A's page answers "completed".
describe("Debug failures after the user opens another execution", () => {
  it("ignores a late page of the earlier execution", async () => {
    const lateAPage = deferred<RunPage>();
    const reads = { a: 0, b: 0 };
    server.runPage.mockImplementation((url) => {
      if (url.includes("/exec-a/")) {
        reads.a += 1;
        return lateAPage.promise;
      }
      reads.b += 1;
      return Promise.resolve(runPage("running"));
    });
    server.kpis.mockResolvedValue(kpis(15));
    route.executionId = "exec-a";
    const { decision, openExecution } = openRunPage();
    await vi.waitFor(() => expect(reads.a).toBe(1));

    await openExecution("exec-b");
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("running"),
    );
    // B starts polling.
    await poll();
    await vi.waitFor(() => expect(reads.b).toBe(2));

    await act(async () => lateAPage.resolve(runPage("completed")));
    await settle();
    // B keeps polling.
    await poll();
    await vi.waitFor(() => expect(reads.b).toBe(3));
    await poll();
    await vi.waitFor(() => expect(reads.b).toBe(4));
    await settle();

    expect(reads.a).toBe(1);
    expect(useTestExecutionStore.getState()).toMatchObject({
      status: "running",
      statusExecutionId: "exec-b",
    });
    expect(decision.current).toEqual(STILL_RUNNING);
  });
});

// A run of 31 calls fills two grid blocks. A poll reads block 1
// while the run is still running, then block 2 just after it finished.
describe("Call rows after a poll that sees the run finish", () => {
  const callRows = (from: number, to: number, status: string): CallRow[] =>
    Array.from({ length: to - from + 1 }, (_, i) => ({
      id: `call-${from + i}`,
      status,
    }));

  it("reads the earlier block again, so its rows get their final state", async () => {
    const run = { finished: false, finishBeforeNextBlock2Read: false };
    const reads: [number, RunStatus][] = [];
    server.runPage.mockImplementation((_url, page) => {
      if (page === 2 && run.finishBeforeNextBlock2Read) run.finished = true;
      const status: RunStatus = run.finished ? "completed" : "running";
      reads.push([page, status]);
      const callStatus = run.finished ? "completed" : "queued";
      return Promise.resolve(
        page === 1
          ? runPage(status, callRows(1, 30, callStatus))
          : runPage(status, callRows(31, 31, callStatus)),
      );
    });
    server.kpis.mockResolvedValue(kpis(15));
    openRunPage({ cachedPages: 2 });
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("running"),
    );
    await settle();

    run.finishBeforeNextBlock2Read = true;
    await poll();
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("completed"),
    );
    // Block 1 was read before the run finished.
    expect(grid.rows.get("call-1")?.status).toBe("queued");
    expect(grid.rows.size).toBe(31);

    const readsBeforeFinalPoll = reads.length;
    await poll();
    await vi.waitFor(() =>
      expect(grid.rows.get("call-1")?.status).toBe("completed"),
    );
    expect(reads.slice(readsBeforeFinalPoll)).toEqual([
      [1, "completed"],
      [2, "completed"],
    ]);
    expect(useTestExecutionStore.getState().status).toBe("completed");

    // Every block read finished: polling stops.
    await poll();
    await settle();
    expect(reads).toHaveLength(readsBeforeFinalPoll + 2);
  });
});
