import type { ReactNode } from "react";
import { act, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The real run grid, its status tracking, the real useKpis and the real
// QueryClient. Only the network and AG Grid's server-side row model are fakes.

type RunStatus = "running" | "completed";
type RunPage = {
  status?: RunStatus;
  count: number;
  results: never[];
  column_order: never[];
};
type Kpis = {
  agent_type: "voice";
  total_calls: number;
  connected_calls: number;
};
type Decision = { disabled: boolean; reason: string };
type GridApi = {
  setGridOption: () => void;
  showNoRowsOverlay: () => void;
  hideOverlay: () => void;
  applyServerSideTransaction: () => void;
  getCacheBlockState: () => Record<number, object>;
};
type GetRowsParams = {
  request: { startRow: number; endRow: number };
  api: GridApi;
  success: () => void;
  fail: () => void;
};
type Datasource = { getRows: (params: GetRowsParams) => Promise<void> };
type GridProps = { serverSideDatasource: Datasource };

const { server, grid } = vi.hoisted(() => {
  const blocks = { count: 1 };
  const api: GridApi = {
    setGridOption: () => {},
    showNoRowsOverlay: () => {},
    hideOverlay: () => {},
    applyServerSideTransaction: () => {},
    getCacheBlockState: () =>
      Object.fromEntries(
        Array.from({ length: blocks.count }, (_, block) => [block, {}]),
      ),
  };
  return {
    // GET /simulate/test-executions/<id>/ and GET .../<id>/kpis/.
    server: {
      runPage: vi.fn<() => Promise<RunPage>>(),
      kpis: vi.fn<() => Promise<Kpis>>(),
    },
    grid: { blocks, api },
  };
});

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  default: {
    get: (url: string) =>
      (url.endsWith("/kpis/") ? server.kpis() : server.runPage()).then(
        (data) => ({ data }),
      ),
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
        for (let block = 0; block < grid.blocks.count; block++) {
          void serverSideDatasource.getRows({
            request: { startRow: block * 30, endRow: (block + 1) * 30 },
            api: grid.api,
            success: () => {},
            fail: () => {},
          });
        }
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
  useParams: () => ({ executionId: "e1" }),
}));
vi.mock("../TestDetailDrawer/TestDetailSideDrawer", () => ({
  default: () => null,
}));
vi.mock("../TestDetailConfigureEval", () => ({ default: () => null }));

const { kpisQueryOptions } = await import("src/hooks/useKpis");
const { useFixMyAgentBlocked } = await import("../common");
const { useTestExecutionStore } = await import("../states");
const { default: TestRunDetailGrid } = await import("../TestRunDetailGrid");

const EXECUTION_ID = "e1";
const POLL_MS = 5000;
const ENABLED: Decision = { disabled: false, reason: "" };
const BLOCKED: Decision = {
  disabled: true,
  reason: "You need at least 15 connected calls to debug failures.",
};
const STILL_RUNNING: Decision = {
  disabled: true,
  reason:
    "The test run is still running. You can debug its failures once it finishes.",
};

const runPage = (status: RunStatus): RunPage => ({
  status,
  count: 0,
  results: [],
  column_order: [],
});
const kpis = (connected_calls: number): Kpis => ({
  agent_type: "voice",
  total_calls: 20,
  connected_calls,
});

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
    decision.current = useFixMyAgentBlocked(EXECUTION_ID);
    return null;
  };
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  render(
    <>
      <TestRunDetailGrid />
      <DebugFailures />
    </>,
    { wrapper: Wrapper },
  );
  return decision;
};

const poll = () =>
  act(async () => {
    vi.advanceTimersByTime(POLL_MS);
  });

beforeEach(() => {
  // Only the grid's 5 s poll is faked; queries and waits run on real timers.
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  server.runPage.mockReset();
  server.kpis.mockReset();
  useTestExecutionStore.setState({ status: null });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Debug failures once the grid sees the run finish", () => {
  it.each<[string, number, number, Decision]>([
    [
      "opens when the final count reaches 15 after a last poll of 14",
      14,
      15,
      ENABLED,
    ],
    [
      "enables after completion when both the cached and final counts are 15",
      15,
      15,
      ENABLED,
    ],
    ["stays blocked when the final count is still 14", 14, 14, BLOCKED],
  ])("%s", async (_name, cached, final, expected) => {
    server.runPage.mockResolvedValue(runPage("running"));
    const decision = openRunPage({ cachedConnectedCalls: cached });
    await vi.waitFor(() => expect(decision.current).toEqual(STILL_RUNNING));
    expect(server.kpis).not.toHaveBeenCalled();

    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(final));
    await poll();

    await vi.waitFor(() => expect(decision.current).toEqual(expected));
    expect(server.kpis).toHaveBeenCalledTimes(1);
  });

  it("decides on a read made after completion when the first read is still in flight", async () => {
    let answerFirstRead: (body: Kpis) => void = () => {};
    server.kpis.mockReturnValueOnce(
      new Promise<Kpis>((resolve) => {
        answerFirstRead = resolve;
      }),
    );
    server.runPage.mockResolvedValue(runPage("running"));
    const decision = openRunPage();
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("running"),
    );
    expect(server.kpis).toHaveBeenCalledTimes(1);

    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(15));
    await poll();
    await vi.waitFor(() => expect(server.kpis).toHaveBeenCalledTimes(2));

    // The first read answers last, with what it saw before the run finished.
    await act(async () => answerFirstRead(kpis(14)));
    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
  });
});

describe("Debug failures on a return visit to a finished run", () => {
  it("reads the final count once, though five cached pages each see the run finished", async () => {
    server.runPage.mockResolvedValue(runPage("completed"));
    server.kpis.mockResolvedValue(kpis(15));
    const decision = openRunPage({ cachedConnectedCalls: 14, cachedPages: 5 });

    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
    expect(server.runPage).toHaveBeenCalledTimes(5);
    expect(server.kpis).toHaveBeenCalledTimes(1);

    // A finished run is not polled.
    await poll();
    expect(server.runPage).toHaveBeenCalledTimes(5);
    expect(server.kpis).toHaveBeenCalledTimes(1);
  });

  it("reuses the first KPI read when the grid reads the run finished before it answers", async () => {
    let answerFirstRead: (body: Kpis) => void = () => {};
    server.kpis.mockReturnValueOnce(
      new Promise<Kpis>((resolve) => {
        answerFirstRead = resolve;
      }),
    );
    server.runPage.mockResolvedValue(runPage("completed"));
    const decision = openRunPage();
    await vi.waitFor(() =>
      expect(useTestExecutionStore.getState().status).toBe("completed"),
    );

    await act(async () => answerFirstRead(kpis(15)));
    await vi.waitFor(() => expect(decision.current).toEqual(ENABLED));
    expect(server.kpis).toHaveBeenCalledTimes(1);
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
      const decision = openRunPage({ cachedConnectedCalls: 14 });
      await vi.waitFor(() => expect(decision.current).toEqual(STILL_RUNNING));

      failTheRead();
      await poll();
      await vi.waitFor(() => expect(server.runPage).toHaveBeenCalledTimes(2));
      // Let the poll finish handling the failed read.
      await act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));
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
