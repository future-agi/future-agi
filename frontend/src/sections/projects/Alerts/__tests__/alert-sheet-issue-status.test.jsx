import React, { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { useAlertSheetView } from "../store/useAlertSheetView";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

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
vi.mock("../store/useAlertStore", () => ({
  useAlertStore: Object.assign(() => mocks.alertStore, {
    getState: () => mocks.alertStore,
  }),
}));
vi.mock("../alertTraceLink", () => ({ buildAlertTraceLink: vi.fn() }));

let client;

// The Issues sheet renders its Status column from the column defs the hook
// stores, so read the real column and render its cell the way the grid does.
const renderStatusCell = (issue) => {
  renderHook(() => useAlertSheetView(), {
    wrapper: ({ children }) =>
      createElement(QueryClientProvider, { client }, children),
  });
  const column = useAlertSheetStore
    .getState()
    .columnDefs.find((def) => def.headerName === "Status");
  const Cell = column.cellRenderer;
  const params = column.cellRendererParams({ data: issue });
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <Cell {...params} />
    </ThemeProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  resetAlertSheetStoreState();
  useAlertSheetStore.setState({
    alertRuleDetails: { id: "monitor-a", project: "project-a" },
  });
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe("Alert issues sheet Status column", () => {
  it("labels an unresolved issue as Open instead of a dash", () => {
    renderStatusCell({ id: "issue-1", resolved: false });

    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.queryByText("-")).not.toBeInTheDocument();
  });

  it("keeps the Resolved label for a resolved issue", () => {
    renderStatusCell({ id: "issue-2", resolved: true });

    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(screen.queryByText("Open")).not.toBeInTheDocument();
  });
});
