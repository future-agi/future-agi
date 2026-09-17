import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import EvalUsageTab from "../EvalUsageTab";
import { AGGREGATION_POLLING_PAUSED_MESSAGE } from "src/utils/queryReadState";

const h = vi.hoisted(() => ({
  chart: {},
  logs: {},
  refetchChart: vi.fn(),
  refetchLogs: vi.fn(),
}));

vi.mock("../../hooks/useEvalUsage", () => ({
  useEvalUsageChart: () => h.chart,
  useEvalUsageLogs: () => h.logs,
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: ({ setDateOption, setParentDateFilter }) => (
    <>
      <button type="button" onClick={() => setDateOption("Custom")}>
        Select custom range
      </button>
      <button
        type="button"
        onClick={() => setParentDateFilter(["2026-08-01", "2026-08-02"])}
      >
        Change custom range
      </button>
    </>
  ),
}));
vi.mock("../UsageChart", () => ({
  default: () => <div data-testid="usage-chart" />,
}));
vi.mock("src/components/data-table", () => {
  const MockDataTable = ({ emptyMessage }) => (
    <div data-testid="usage-table">{emptyMessage}</div>
  );
  MockDataTable.propTypes = { emptyMessage: PropTypes.string };
  const MockDataTablePagination = ({ page, onPageChange }) => (
    <div data-testid="usage-pagination">
      <span>Page {page}</span>
      <button type="button" onClick={() => onPageChange(3)}>
        Go to page 4
      </button>
    </div>
  );
  MockDataTablePagination.propTypes = {
    page: PropTypes.number,
    onPageChange: PropTypes.func,
  };

  return {
    DataTable: MockDataTable,
    DataTablePagination: MockDataTablePagination,
  };
});
vi.mock("../../Helpers/evalUsageColumns", () => ({
  COLUMN_CONFIG_URL_PARAM: "columns",
  DATE_OPTION_TO_PERIOD: { "30D": "30d" },
  DEFAULT_COLUMN_CONFIG: [],
  ScoreCell: () => null,
  StatPill: Object.assign(
    ({ label, value }) => (
      <span>
        {label}: {value}
      </span>
    ),
    {
      propTypes: {
        label: PropTypes.string,
        value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
      },
    },
  ),
  columnConfigStorageKey: () => "eval-usage-columns",
  decodeColumnConfig: () => null,
  encodeColumnConfig: () => "",
  normalizeRow: (row) => row,
  periodForRange: () => "30d",
  useColumns: () => [],
}));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/sections/common/EvalsTasks/PartialInputWarningDetails", () => ({
  default: () => null,
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/hooks/use-local-storage", () => ({
  getStorage: () => null,
  setStorage: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "viewer" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { EDIT_CREATE_DELETE_EVALS: "edit" },
  RolePermission: { EVALS: { edit: { viewer: false } } },
}));

describe("EvalUsageTab exact read states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.chart = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      refresh: h.refetchLogs,
    };
  });

  it("uses generic retry states and does not present failures as zero data", () => {
    render(<EvalUsageTab templateId="eval-1" />);

    expect(
      screen.getAllByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toHaveLength(2);
    expect(screen.queryByText("Loading results…")).not.toBeInTheDocument();
    expect(screen.queryByText(/No data to show/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("usage-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("usage-pagination")).not.toBeInTheDocument();

    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    fireEvent.click(retryButtons[0]);
    expect(h.refetchChart).toHaveBeenCalledOnce();
    expect(h.refetchLogs).toHaveBeenCalledOnce();
    fireEvent.click(retryButtons[1]);
    expect(h.refetchLogs).toHaveBeenCalledTimes(2);
  });

  it("enables refresh and both retries when terminal failures retain pending server metadata", () => {
    h.chart = {
      data: {
        stats: {},
        chart: [],
        queryPending: true,
        queryRefreshing: true,
      },
      isLoading: false,
      isFetching: false,
      isError: true,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: {
        table: [],
        pagination: {},
        queryPending: true,
        queryRefreshing: true,
      },
      isLoading: false,
      isFetching: false,
      isError: true,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    expect(retryButtons).toHaveLength(2);

    fireEvent.click(retryButtons[0]);
    expect(h.refetchChart).toHaveBeenCalledOnce();
    expect(h.refetchLogs).toHaveBeenCalledOnce();
    fireEvent.click(retryButtons[1]);
    expect(h.refetchLogs).toHaveBeenCalledTimes(2);
  });

  it("does not publish empty chart or table states before the first response", () => {
    h.chart = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(screen.getAllByText("Loading results…")).toHaveLength(2);
    expect(screen.queryByText(/No data to show/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("usage-table")).not.toBeInTheDocument();
    expect(screen.queryByText(/Runs: 0/i)).not.toBeInTheDocument();
  });

  it("keeps the genuine successful-empty state distinct", () => {
    h.chart = {
      data: {
        stats: {},
        chart: [],
        queryCompletedAt: "2026-08-03T02:00:00Z",
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: { table: [], pagination: { total: 0 } },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(
      screen.getByText(/No data to show for selected period/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("usage-table")).toHaveTextContent(
      "No evaluation logs for this period",
    );
    expect(screen.getByTestId("usage-pagination")).toBeInTheDocument();
    expect(screen.queryByText("Loading results…")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Last updated/i)).toBeInTheDocument();
  });

  it("returns to the first page when an existing Custom range changes", () => {
    h.chart = {
      data: { stats: {}, chart: [] },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: { table: [], pagination: { total: 100 } },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    fireEvent.click(
      screen.getByRole("button", { name: "Select custom range" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Go to page 4" }));
    expect(screen.getByText("Page 3")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Change custom range" }),
    );
    expect(screen.getByText("Page 0")).toBeInTheDocument();
  });

  it("retains prior exact chart and table data when a manual refresh fails", () => {
    h.chart = {
      data: {
        stats: { runs_period: 4 },
        chart: [{ timestamp: "2026-08-03T00:00:00Z", calls: 4 }],
        queryCompletedAt: "2026-08-03T02:00:00Z",
      },
      isLoading: false,
      isFetching: false,
      isError: true,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: {
        table: [{ id: "row-1" }],
        pagination: { total: 1 },
      },
      isLoading: false,
      isFetching: false,
      isError: true,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(screen.getByTestId("usage-chart")).toBeInTheDocument();
    expect(screen.getByTestId("usage-table")).toBeInTheDocument();
    expect(screen.getByTestId("usage-pagination")).toBeInTheDocument();
    expect(screen.queryByText("Loading results…")).not.toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(h.refetchChart).toHaveBeenCalledOnce();
    expect(h.refetchLogs).toHaveBeenCalledOnce();
  });

  it("keeps cached exact data visible and the refresh action spinning while queued", () => {
    h.chart = {
      data: {
        stats: { runs_period: 4 },
        chart: [{ timestamp: "2026-08-03T00:00:00Z", calls: 4 }],
        queryCompletedAt: "2026-08-03T02:00:00Z",
        queryPending: false,
        queryRefreshing: true,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: {
        table: [{ id: "row-1" }],
        pagination: { total: 1 },
        queryPending: false,
        queryRefreshing: true,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(screen.getByTestId("usage-chart")).toBeInTheDocument();
    expect(screen.getByTestId("usage-table")).toBeInTheDocument();
    expect(screen.queryByText("Loading results…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refreshing" })).toBeDisabled();
  });

  it("preserves the prior exact chart and log page when polling returns pending", () => {
    h.chart = {
      data: {
        stats: { runs_period: 4 },
        chart: [{ timestamp: "2026-08-03T00:00:00Z", calls: 4 }],
        queryCompletedAt: "2026-08-03T02:00:00Z",
        queryPending: false,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: {
        table: [{ id: "row-1" }],
        pagination: { total: 1 },
        queryPending: false,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchLogs,
    };

    const view = render(<EvalUsageTab templateId="eval-1" />);
    expect(screen.getByTestId("usage-chart")).toBeInTheDocument();
    expect(screen.getByText(/Runs: 4/i)).toBeInTheDocument();

    h.chart = {
      ...h.chart,
      data: {
        stats: {},
        chart: [],
        queryPending: true,
        queryRefreshing: true,
      },
    };
    h.logs = {
      ...h.logs,
      data: {
        table: [],
        pagination: { total: 0 },
        queryPending: true,
        queryRefreshing: true,
      },
    };
    view.rerender(<EvalUsageTab templateId="eval-1" />);

    expect(screen.getByTestId("usage-chart")).toBeInTheDocument();
    expect(screen.getByTestId("usage-table")).toBeInTheDocument();
    expect(screen.getByText(/Runs: 4/i)).toBeInTheDocument();
    expect(screen.queryByText("Loading results…")).not.toBeInTheDocument();
    expect(screen.queryByText(/Runs: 0/i)).not.toBeInTheDocument();
  });

  it("treats a cold pending response as preparation rather than exact empty data", () => {
    h.chart = {
      data: {
        stats: {},
        chart: [],
        queryPending: true,
        queryRefreshing: true,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: {
        table: [],
        pagination: {},
        queryPending: true,
        queryRefreshing: true,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(screen.getAllByText("Loading results…")).toHaveLength(2);
    expect(screen.queryByText(/No data to show/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("usage-table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refreshing" })).toBeDisabled();
  });

  it("presents an exhausted polling budget as paused with manual retry", () => {
    h.chart = {
      data: {
        stats: {},
        chart: [],
        queryPending: true,
        queryRefreshing: false,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      isPollingPaused: true,
      refresh: h.refetchChart,
    };
    h.logs = {
      data: {
        table: [],
        pagination: {},
        queryPending: true,
        queryRefreshing: false,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      isPollingPaused: true,
      refresh: h.refetchLogs,
    };

    render(<EvalUsageTab templateId="eval-1" />);

    expect(
      screen.getAllByText(AGGREGATION_POLLING_PAUSED_MESSAGE),
    ).toHaveLength(2);
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();

    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    fireEvent.click(retryButtons[0]);
    expect(h.refetchChart).toHaveBeenCalledOnce();
    expect(h.refetchLogs).toHaveBeenCalledOnce();
    fireEvent.click(retryButtons[1]);
    expect(h.refetchLogs).toHaveBeenCalledTimes(2);
  });
});
