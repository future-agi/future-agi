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
import { resetAlertSheetFilterStoreState } from "../store/useAlertSheetFilterStore";

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

// The same functional update the Issues grid sends when a row is ticked or
// unticked while every issue is selected (AlertsSheetView/Issues.jsx).
const toggleExclusion = (rowId, isSelected) => (prev) => {
  const next = new Set(prev);
  if (!isSelected) {
    next.add(rowId);
  } else {
    next.delete(rowId);
  }
  return next;
};

let client;
const mountHook = () =>
  renderHook(() => useAlertSheetView(), {
    wrapper: ({ children }) =>
      createElement(QueryClientProvider, { client }, children),
  });
const setRowSelected = (result, rowId, isSelected) => {
  act(() => {
    result.current.setExcludingIds(toggleExclusion(rowId, isSelected));
  });
};
const exclusions = () => useAlertSheetStore.getState().excludingIds;
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

describe("alert sheet store exclusion updates", () => {
  it("applies a functional update to the current exclusions", () => {
    useAlertSheetStore.setState({ excludingIds: new Set(["issue-a"]) });
    const { setExcludingIds } = useAlertSheetStore.getState();

    setExcludingIds((prev) => new Set([...prev, "issue-b"]));

    expect(exclusions()).toBeInstanceOf(Set);
    expect(Array.from(exclusions())).toEqual(["issue-a", "issue-b"]);
  });

  it("chains functional updates as issues are unticked and ticked again", () => {
    const { setExcludingIds } = useAlertSheetStore.getState();

    setExcludingIds(toggleExclusion("issue-x", false));
    setExcludingIds(toggleExclusion("issue-y", false));
    setExcludingIds(toggleExclusion("issue-x", true));

    expect(exclusions()).toBeInstanceOf(Set);
    expect(Array.from(exclusions())).toEqual(["issue-y"]);
  });

  it("still replaces the exclusions when given a set", () => {
    const { setExcludingIds } = useAlertSheetStore.getState();

    setExcludingIds(new Set(["issue-z"]));
    expect(Array.from(exclusions())).toEqual(["issue-z"]);

    setExcludingIds(new Set());
    expect(exclusions()).toBeInstanceOf(Set);
    expect(exclusions().size).toBe(0);
  });
});

describe("monitor drawer select-all with unticked issues", () => {
  it("counts every issue unticked from a select-all", () => {
    const rows = issueRows(105);
    const { result } = mountHook();

    setRowSelected(result, rows[0].id, false);
    setRowSelected(result, rows[1].id, false);

    // The selection bar shows totalRows - excludingIds.length (Issues.jsx).
    const { excludingIds, totalRows } = result.current;
    expect(excludingIds).toEqual([rows[0].id, rows[1].id]);
    expect(totalRows - excludingIds.length).toBe(103);

    setRowSelected(result, rows[0].id, true);
    expect(result.current.excludingIds).toEqual([rows[1].id]);
  });

  it("resolves the remaining issues of the monitor after unticking some", async () => {
    const rows = issueRows(105);
    axios.get
      .mockResolvedValueOnce(page(rows.slice(0, 100), 105))
      .mockResolvedValueOnce(page(rows.slice(100), 105, 1));
    const { result } = mountHook();

    setRowSelected(result, rows[0].id, false);
    setRowSelected(result, rows[104].id, false);
    resolveSelection(result);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await waitForMutation();

    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.resolveAlerts, {
      log_ids: rows.slice(1, 104).map((row) => row.id),
    });
    expect(enqueueSnackbar).toHaveBeenCalledWith("Resolved", {
      variant: "success",
    });
    expect(useAlertSheetStore.getState().selectedAll).toBe(false);
  });
});
