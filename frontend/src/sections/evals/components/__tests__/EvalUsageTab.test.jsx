/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";


const themeWithAppTypography = createTheme({
  palette: palette("light"),
  typography,
});

const { chartHook, logsHook } = vi.hoisted(() => ({
  chartHook: vi.fn(),
  logsHook: vi.fn(),
}));

vi.mock("../../hooks/useEvalUsage", () => ({
  useEvalUsageChart: (...args) => chartHook(...args),
  useEvalUsageLogs: (...args) => logsHook(...args),
}));


vi.mock("../UsageChart", () => ({
  default: ({ data, outputType }) => (
    <div data-testid="usage-chart" data-output-type={outputType}>
      {data.length} points
    </div>
  ),
}));

vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: ({ dateOption, setDateOption }) => (
    <div data-testid="date-range-picker">
      <span>{dateOption}</span>
      <button onClick={() => setDateOption("7D")}>Set 7D</button>
    </div>
  ),
}));

vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));

vi.mock("@monaco-editor/react", () => ({
  default: ({ value }) => <pre data-testid="json-editor">{value}</pre>,
}));

vi.mock("src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer", () => ({
  default: () => null,
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data, onRowClick, emptyMessage }) => {
    if (!data.length) return <div>{emptyMessage}</div>;
    return (
      <div data-testid="usage-table">
        {data.map((row) => (
          <div
            key={row.id}
            data-testid={`usage-row-${row.id}`}
            onClick={() => onRowClick(row)}
          >
            {columns.map((col) => {
              const value = row[col.accessorKey || col.id];
              return (
                <span key={col.id}>
                  {col.cell
                    ? col.cell({
                        getValue: () => value,
                        row: { original: row },
                      })
                    : value}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    );
  },
  DataTablePagination: () => <div data-testid="usage-pagination" />,
}));

import EvalUsageTab from "../EvalUsageTab";

function renderTab(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(
    <Wrapper>
      <EvalUsageTab templateId="tmpl-1" {...props} />
    </Wrapper>,
    { theme: themeWithAppTypography },
  );
}

const baseLogRow = {
  row_id: "row-1",
  score: { cell_value: 0.9 },
  result: { cell_value: "Passed" },
  input: { cell_value: "What is 2+2?" },
  reason: { cell_value: "Correct answer" },
  source: { cell_value: "eval_playground" },
  version: { cell_value: 1 },
  created_at: { cell_value: "2026-08-01T12:00:00Z" },
  status: "success",
};

describe("EvalUsageTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chartHook.mockReturnValue({
      data: {
        stats: { runs_period: 12, success_count: 10, error_count: 2, pass_rate: 80 },
        chart: [{ timestamp: "2026-08-01T00:00:00Z", calls: 5 }],
      },
      isLoading: false,
    });
    logsHook.mockReturnValue({
      data: { table: [baseLogRow], pagination: { total: 1 } },
      isLoading: false,
      isFetching: false,
    });
  });

  it("renders usage stats from the chart hook", () => {
    renderTab();

    expect(screen.getByText(/Runs:/)).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
  });

  it("renders the chart when chart data is present", () => {
    renderTab();

    const chart = screen.getByTestId("usage-chart");
    expect(chart).toHaveAttribute("data-output-type", "pass_fail");
    expect(chart).toHaveTextContent("1 points");
  });

  it("shows a helper message instead of the chart when there is no chart data", () => {
    chartHook.mockReturnValue({
      data: { stats: {}, chart: [] },
      isLoading: false,
    });

    renderTab();

    expect(screen.queryByTestId("usage-chart")).not.toBeInTheDocument();
    expect(
      screen.getByText(/No data to show for selected period/i),
    ).toBeInTheDocument();
  });

  it("renders log rows from the logs hook, unwrapping cell_value", () => {
    renderTab();

    const row = screen.getByTestId("usage-row-row-1");
    expect(row).toHaveTextContent("Passed");
    expect(row).toHaveTextContent("What is 2+2?");
  });

  it("shows the empty-logs message when there are no logs", () => {
    logsHook.mockReturnValue({
      data: { table: [], pagination: { total: 0 } },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    expect(
      screen.getByText("No evaluation logs for this period"),
    ).toBeInTheDocument();
  });

  it("filters visible rows by the search box (id/input/result/reason)", async () => {
    logsHook.mockReturnValue({
      data: {
        table: [
          baseLogRow,
          {
            ...baseLogRow,
            row_id: "row-2",
            input: { cell_value: "Unrelated question" },
            result: { cell_value: "Failed" },
          },
        ],
        pagination: { total: 2 },
      },
      isLoading: false,
      isFetching: false,
    });

    renderTab();

    expect(screen.getByTestId("usage-row-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("usage-row-row-2")).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("Search..."), "2+2");

    // Debounced (400ms) — wait for the non-matching row to drop out rather
    // than asserting on the still-present row-1, which would pass instantly.
    await waitFor(() =>
      expect(screen.queryByTestId("usage-row-row-2")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("usage-row-row-1")).toBeInTheDocument();
  });

  it("opens the detail side panel with the row's fields on row click", async () => {
    renderTab();

    await userEvent.click(screen.getByTestId("usage-row-row-1"));

    // "Status" is a detail-panel-only field (not one of the table's default
    // columns), so it unambiguously confirms the panel opened with this row.
    expect(await screen.findByText("success")).toBeInTheDocument();
    // Prev/next counter in the panel header confirms the row index.
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
  });

  it("switches the date range preset via DateTimeRangePicker", async () => {
    renderTab();

    await userEvent.click(screen.getByText("Set 7D"));

    expect(screen.getByText("7D")).toBeInTheDocument();
  });

  it("renders a loading skeleton for the chart while chart data is loading", () => {
    chartHook.mockReturnValue({ data: undefined, isLoading: true });

    renderTab();

    expect(screen.queryByTestId("usage-chart")).not.toBeInTheDocument();
    expect(screen.queryByText(/Runs:/)).not.toBeInTheDocument();
  });
});
