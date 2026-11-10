import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import TestRunDetailGrid from "../TestRunDetailGrid";
import TestRunDetailHeader from "../TestRunDetailHeader";
import {
  useTestDetailStore,
  useTestDetailSideDrawerStore,
  useTestExecutionStore,
} from "../states";

const h = vi.hoisted(() => ({
  gridProps: null,
  api: {
    getCacheBlockState: vi.fn(() => ({ 0: {} })),
    setGridOption: vi.fn(),
    hideOverlay: vi.fn(),
    showNoRowsOverlay: vi.fn(),
    applyServerSideTransaction: vi.fn(),
  },
  context: { setGridApi: vi.fn(), refreshGrid: vi.fn() },
}));

// Keep the real datasource, query adapter, status list, and Zustand stores.
// Only replace AG Grid's DOM machinery and unrelated visual children.
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    AgGridReact: forwardRef(function MockGrid(props, ref) {
      h.gridProps = props;
      useImperativeHandle(ref, () => ({ api: h.api }), []);
      return null;
    }),
  };
});
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));
vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: () => [null, vi.fn()],
}));
vi.mock("../context/TestDetailContext", () => ({
  useTestDetail: () => h.context,
}));
vi.mock("../TestDetailDrawer/TestDetailSideDrawer", () => ({
  default: () => null,
}));
vi.mock("../TestDetailConfigureEval", () => ({ default: () => null }));
vi.mock(
  "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider",
  () => ({ default: ({ children }) => children }),
);
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: ({ children }) => children }),
);
vi.mock("src/sections/develop-detail/DataTab/common", async (importOriginal) => ({
  ...(await importOriginal()),
  postProcessPopup: vi.fn(),
}));
vi.mock("src/hooks/useKpis", () => ({
  default: () => ({ data: { agent_type: "chat", total_calls: 1 } }),
}));
vi.mock("src/hooks/useTestRunDetails", () => ({
  default: () => ({ data: { source_type: "agent" } }),
}));
vi.mock("src/sections/test/common", async (importOriginal) => ({
  ...(await importOriginal()),
  useCancelExecution: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/api/tests/testDetails", async (importOriginal) => ({
  ...(await importOriginal()),
  useRerunTest: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/sections/test/TestRuns/NewVoiceSimulationDrawer", () => ({
  default: () => null,
}));
vi.mock("../TestDetailBreadcrumb", () => ({ default: () => null }));
vi.mock("../components/CallMetadataSection", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

const detailResponse = (status) => ({
  data: {
    status,
    count: 1,
    column_order: [],
    results: [{ id: "call-1", status }],
  },
});

let queryClient;
let getDetail;

const renderDetail = async (status) => {
  getDetail.mockResolvedValue(detailResponse(status));
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/test/test-1/execution-1"]}>
        <Routes>
          <Route
            path="/test/:testId/:executionId"
            element={
              <>
                <TestRunDetailHeader />
                <TestRunDetailGrid />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  const success = vi.fn();
  const fail = vi.fn();
  await act(async () => {
    await h.gridProps.serverSideDatasource.getRows({
      request: { startRow: 0, endRow: 30 },
      api: h.api,
      success,
      fail,
    });
  });
  expect(fail).not.toHaveBeenCalled();
  expect(success).toHaveBeenCalledOnce();
  expect(getDetail).toHaveBeenCalledExactlyOnceWith(
    endpoints.testExecutions.list("execution-1"),
    { params: { page: 1, limit: 30, search: "", filters: "[]" } },
  );
  expect(useTestExecutionStore.getState().status).toBe(status);
};

const tickPoll = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
};

const expectStopVisible = () => {
  expect(screen.getByRole("button", { name: "Stop Running" })).toBeVisible();
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  h.gridProps = null;
  useTestDetailStore.getState().reset();
  useTestDetailStore.setState({ filters: [] });
  useTestDetailSideDrawerStore.setState({ testDetailDrawerOpen: null });
  useTestExecutionStore.setState({ status: null });
  getDetail = vi.spyOn(axios, "get");
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("test run detail active status consumers", () => {
  it.each([
    "queued",
    "ongoing",
    "analyzing",
    "pending",
    "running",
    "evaluating",
    "cancelling",
  ])("starts and retains polling and the stop control for %s", async (status) => {
    await renderDetail(status);
    expectStopVisible();

    await tickPoll();
    expect(getDetail).toHaveBeenCalledTimes(2);
    expectStopVisible();

    await tickPoll();
    expect(getDetail).toHaveBeenCalledTimes(3);
    expectStopVisible();
  });

  it.each(["queued", "ongoing", "analyzing"])(
    "keeps polling when pending advances to %s, then stops on completion",
    async (status) => {
      await renderDetail("pending");
      expectStopVisible();
      getDetail.mockResolvedValue(detailResponse(status));

      await tickPoll();
      expect(getDetail).toHaveBeenCalledTimes(2);
      expect(useTestExecutionStore.getState().status).toBe(status);
      expectStopVisible();

      // A refresh with an unrecognized active status used to disable the timer.
      await tickPoll();
      expect(getDetail).toHaveBeenCalledTimes(3);
      expectStopVisible();

      getDetail.mockResolvedValue(detailResponse("completed"));
      await tickPoll();
      expect(getDetail).toHaveBeenCalledTimes(4);
      expect(useTestExecutionStore.getState().status).toBe("completed");
      expect(
        screen.queryByRole("button", { name: "Stop Running" }),
      ).not.toBeInTheDocument();

      await tickPoll();
      expect(getDetail).toHaveBeenCalledTimes(4);
    },
  );

  it.each(["completed", "failed", "cancelled"])(
    "does not start polling or show the stop control for %s",
    async (status) => {
      await renderDetail(status);
      expect(
        screen.queryByRole("button", { name: "Stop Running" }),
      ).not.toBeInTheDocument();
      await tickPoll();
      await tickPoll();
      expect(getDetail).toHaveBeenCalledOnce();
    },
  );
});
