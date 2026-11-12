import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useAlertSheetView } from "../store/useAlertSheetView";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";
import {
  resetAlertSheetFilterStoreState,
  useAlertSheetFilterStore,
} from "../store/useAlertSheetFilterStore";

const mocks = vi.hoisted(() => ({
  alertStore: { openSheetView: "monitor-a" },
  navigate: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    project: {
      getAlertDetails: (id) => `/monitors/${id}/details/`,
      resolveAlerts: "/issues/resolve/",
    },
  },
}));
vi.mock("react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { alertIssueResolvedClicked: "issue-resolve-clicked" },
  PropertyName: { id: "id", list: "list" },
  trackEvent: vi.fn(),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("@mui/material", () => ({ Typography: () => null }));
vi.mock("../components/AlertsListView/AlertCells", () => ({
  ActionCell: () => null,
  IssueNameCell: () => null,
  StatusCell: () => null,
}));
vi.mock("../store/useAlertStore", () => ({
  useAlertStore: Object.assign(() => mocks.alertStore, {
    getState: () => mocks.alertStore,
  }),
}));
vi.mock("../alertTraceLink", () => ({ buildAlertTraceLink: vi.fn() }));

const issueRows = (count) =>
  Array.from({ length: count }, (_, index) => ({
    id: `monitor-a-issue-${index}`,
    type: "critical",
  }));

const page = (rows, totalRows, pageNumber = 0) => ({
  data: {
    result: {
      id: "monitor-a",
      logs: {
        results: rows,
        metadata: {
          page_number: pageNumber,
          page_size: 100,
          total_rows: totalRows,
          total_pages: Math.ceil(totalRows / 100),
        },
      },
    },
  },
});

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

let client;
const mountHook = () =>
  renderHook(() => useAlertSheetView(), {
    wrapper: ({ children }) =>
      createElement(QueryClientProvider, { client }, children),
  });
const resolveSelection = (result) => {
  act(() => result.current.handleResolveAlerts());
};
const waitForMutation = () =>
  waitFor(() => expect(client.isMutating()).toBe(0));

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockReset();
  axios.post.mockReset().mockResolvedValue({ data: { result: "Resolved" } });
  mocks.alertStore = { openSheetView: "monitor-a" };
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  resetAlertSheetStoreState();
  resetAlertSheetFilterStoreState();
  useAlertSheetStore.setState({
    selectedAll: true,
    totalRows: 105,
    alertRuleDetails: { id: "monitor-a", project: "project-a" },
    gridRef: {
      current: {
        api: { deselectAll: vi.fn(), refreshServerSide: vi.fn() },
      },
    },
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  resetAlertSheetStoreState();
  resetAlertSheetFilterStoreState();
});

describe("monitor drawer select-all resolution", () => {
  it("resolves all pages of the current monitor with the active type and exclusions", async () => {
    const rows = issueRows(105);
    const exclusions = new Set([rows[0].id, rows[104].id]);
    useAlertSheetStore.setState({
      excludingIds: exclusions,
      searchQuery: "latency",
    });
    useAlertSheetFilterStore
      .getState()
      .setActiveFilters({ type: ["critical"] });
    axios.get
      .mockResolvedValueOnce(page(rows.slice(0, 100), 105))
      .mockResolvedValueOnce(page(rows.slice(100), 105, 1));
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await waitForMutation();

    expect(axios.get).toHaveBeenCalledTimes(2);
    for (const pageNumber of [0, 1]) {
      expect(axios.get).toHaveBeenNthCalledWith(
        pageNumber + 1,
        endpoints.project.getAlertDetails("monitor-a"),
        {
          params: {
            page_number: pageNumber,
            page_size: 100,
            search_text: "latency",
            type: "critical",
          },
        },
      );
    }
    // Exact body equality rules out both workspace-wide select_all and IDs
    // from another monitor. Every ID must come from the scoped details pages.
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.resolveAlerts, {
      log_ids: rows
        .filter((row) => !exclusions.has(row.id))
        .map((row) => row.id),
    });
    expect(useAlertSheetStore.getState().selectedAll).toBe(false);
    expect(
      useAlertSheetStore.getState().gridRef.current.api.refreshServerSide,
    ).toHaveBeenCalledTimes(1);
  });

  it("keeps the selection snapshot and pending state throughout enumeration", async () => {
    const rows = issueRows(101);
    const firstPage = deferred();
    const post = deferred();
    useAlertSheetStore.setState({ excludingIds: new Set([rows[0].id]) });
    useAlertSheetFilterStore
      .getState()
      .setActiveFilters({ type: ["critical"] });
    axios.get
      .mockReturnValueOnce(firstPage.promise)
      .mockResolvedValueOnce(page(rows.slice(100), 101, 1));
    axios.post.mockReturnValueOnce(post.promise);
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.isResolving).toBe(true));
    expect(axios.post).not.toHaveBeenCalled();

    act(() => {
      mocks.alertStore = { openSheetView: "monitor-b" };
      useAlertSheetStore.setState({
        alertRuleDetails: { id: "monitor-b" },
        excludingIds: new Set([rows[100].id]),
        searchQuery: "different search",
      });
      useAlertSheetFilterStore
        .getState()
        .setActiveFilters({ type: ["warning"] });
    });
    await act(async () => firstPage.resolve(page(rows.slice(0, 100), 101)));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));

    expect(axios.get).toHaveBeenNthCalledWith(
      2,
      endpoints.project.getAlertDetails("monitor-a"),
      {
        params: {
          page_number: 1,
          page_size: 100,
          search_text: "",
          type: "critical",
        },
      },
    );
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.resolveAlerts, {
      log_ids: rows.slice(1).map((row) => row.id),
    });
    expect(result.current.isResolving).toBe(true);
    await act(async () => post.resolve({ data: { result: "Resolved" } }));
    await waitFor(() => expect(result.current.isResolving).toBe(false));
  });

  it("does not resolve a partial selection when a later page fails", async () => {
    const rows = issueRows(100);
    axios.get
      .mockResolvedValueOnce(page(rows, 105))
      .mockRejectedValueOnce(new Error("Details request failed"));
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), {
        variant: "error",
      }),
    );
    await waitForMutation();

    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(axios.post).not.toHaveBeenCalled();
    expect(useAlertSheetStore.getState().selectedAll).toBe(true);
    expect(
      useAlertSheetStore.getState().gridRef.current.api.refreshServerSide,
    ).not.toHaveBeenCalled();
  });

  it("does not resolve when pagination metadata is missing", async () => {
    axios.get.mockResolvedValue({
      data: { result: { logs: { results: issueRows(1) } } },
    });
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), {
        variant: "error",
      }),
    );
    await waitForMutation();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("does not resolve without a current monitor", async () => {
    mocks.alertStore = { openSheetView: null };
    const { result } = mountHook();

    resolveSelection(result);
    await waitForMutation();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("does not resolve stale details belonging to another monitor", async () => {
    mocks.alertStore = { openSheetView: "monitor-b" };
    const { result } = mountHook();

    resolveSelection(result);
    await waitForMutation();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("does not post an empty selection when no matching issues remain", async () => {
    axios.get.mockResolvedValueOnce(page([], 0));
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await waitForMutation();
    expect(axios.post).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("does not post when every matching issue is excluded", async () => {
    const rows = issueRows(21);
    useAlertSheetStore.setState({
      excludingIds: new Set(rows.map((row) => row.id)),
    });
    axios.get.mockResolvedValueOnce(page(rows, 21));
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await waitForMutation();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("deduplicates overlapping pages without adding a type filter", async () => {
    const rows = issueRows(104);
    axios.get
      .mockResolvedValueOnce(page(rows.slice(0, 100), 105))
      .mockResolvedValueOnce(page(rows.slice(99), 105, 1));
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await waitForMutation();
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.resolveAlerts, {
      log_ids: rows.map((row) => row.id),
    });
    expect(
      axios.get.mock.calls.every(([, config]) => !("type" in config.params)),
    ).toBe(true);
  });
});

describe("monitor drawer resolution controls", () => {
  it("keeps explicit row resolution on the existing log_ids contract", async () => {
    useAlertSheetStore.setState({
      selectedAll: false,
      selectedRows: issueRows(2),
    });
    const { result } = mountHook();

    resolveSelection(result);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await waitForMutation();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.resolveAlerts, {
      log_ids: ["monitor-a-issue-0", "monitor-a-issue-1"],
    });
  });

  it("keeps the single-row action scoped to its issue", async () => {
    mountHook();
    const actions = useAlertSheetStore
      .getState()
      .columnDefs.find((column) => column.field === "actions");

    act(() =>
      actions.cellRendererParams({ data: issueRows(1)[0] }).onClick("resolve"),
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await waitForMutation();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.resolveAlerts, {
      log_ids: ["monitor-a-issue-0"],
    });
  });

  it("does nothing without a selection", async () => {
    useAlertSheetStore.setState({ selectedAll: false, selectedRows: [] });
    const { result } = mountHook();

    resolveSelection(result);
    await waitForMutation();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });
});
