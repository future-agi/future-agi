import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SQLQueryWidget from "./SQLQueryWidget";
import useSQLExecution from "./useSQLExecution";

vi.mock("./useSQLExecution", () => ({ default: vi.fn() }));
vi.mock("./QueryResults", () => ({
  default: () => <div>SQL result grid</div>,
}));

const base = {
  widget: {
    id: "widget",
    chart_config: {},
    query_config: {
      source: "sql",
      project_id: "project",
      saved_query_id: "saved",
    },
  },
  refreshRequestId: 0,
  dashboardId: "dashboard",
  onQuerySettled: vi.fn(),
};
beforeEach(() => {
  vi.clearAllMocks();
  useSQLExecution.mockReturnValue({
    job: {
      state: "succeeded",
      created_at: new Date().toISOString(),
      result: { truncated: false },
    },
    busy: false,
    error: "",
    run: vi.fn(),
    cancel: vi.fn(),
  });
});
const renderWidget = (props) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <SQLQueryWidget {...props} />
    </ThemeProvider>,
  );

describe("SQL dashboard widget time boundaries", () => {
  it("executes a saved query for valid dashboard windows", () => {
    renderWidget({
      ...base,
      globalDateRange: {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-10-21T00:00:00.000Z",
      },
    });
    expect(useSQLExecution.mock.results[0].value.run).toHaveBeenCalledWith(
      {
        saved_query_id: "saved",
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-10-21T00:00:00.000Z",
      },
      true,
    );
    expect(screen.getByText("SQL result grid")).toBeInTheDocument();
  });
  it("does not silently truncate a dashboard range longer than the catalog window", () => {
    const cancel = vi.fn();
    useSQLExecution.mockReturnValue({
      job: { state: "running" },
      busy: true,
      error: "",
      run: vi.fn(),
      cancel,
    });
    renderWidget({
      ...base,
      refreshRequestId: 1,
      globalDateRange: {
        start: "2026-01-01T00:00:00.000Z",
        end: "2026-10-21T00:00:00.000Z",
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "SQL dashboard queries support windows up to 31 days",
    );
    expect(useSQLExecution.mock.results[0].value.run).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalled();
    expect(base.onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({
        dashboardId: "dashboard",
        widgetId: "widget",
        refreshRequestId: 1,
        manualRefresh: true,
        exact: false,
        pollingPaused: true,
      }),
    );
    expect(screen.queryByText("SQL result grid")).toBeNull();
  });
  it("offers a bounded retry after a saved query execution fails", () => {
    const run = vi.fn();
    useSQLExecution.mockReturnValue({
      job: { state: "failed", error: "Query failed" },
      busy: false,
      error: "",
      run,
      cancel: vi.fn(),
    });
    renderWidget({ ...base, globalDateRange: null });
    expect(screen.getByRole("alert")).toHaveTextContent("Query failed");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(run).toHaveBeenCalledWith({ saved_query_id: "saved" }, true);
  });
});
