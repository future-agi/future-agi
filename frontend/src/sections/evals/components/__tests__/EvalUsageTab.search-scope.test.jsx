import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import EvalUsageTab from "../EvalUsageTab";

// W10-EVALS-U05: Usage log search runs on the loaded page only. It must say
// so and never claim the period has no logs when the search missed this page.

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
  default: () => null,
}));
vi.mock("../UsageChart", () => ({
  default: () => <div data-testid="usage-chart" />,
}));
vi.mock("src/components/data-table", () => {
  // Mirrors DataTable's empty overlay: `emptyContent` wins over
  // `emptyMessage` when the page has no rows to show.
  const MockDataTable = ({ data = [], emptyMessage, emptyContent }) => (
    <div data-testid="usage-table">
      {data.length === 0
        ? emptyContent ?? emptyMessage
        : data.map((row) => (
            <div key={row.id} data-testid="usage-row">
              {row.input}
            </div>
          ))}
    </div>
  );
  MockDataTable.propTypes = {
    data: PropTypes.array,
    emptyMessage: PropTypes.string,
    emptyContent: PropTypes.node,
  };
  const MockDataTablePagination = ({ total }) => (
    <div data-testid="usage-pagination">{`1-25 of ${total}`}</div>
  );
  MockDataTablePagination.propTypes = { total: PropTypes.number };
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
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({ default: () => null }),
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

const PAGE_ONE = [
  { id: "log-1", input: "What is the refund window?", result: "Passed" },
  { id: "log-2", input: "Can I change my plan?", result: "Failed" },
];

const exact = (data, refresh) => ({
  data,
  isLoading: false,
  isFetching: false,
  isError: false,
  refresh,
});

const search = (value) =>
  fireEvent.change(screen.getByRole("textbox", { name: "Search this page" }), {
    target: { value },
  });

describe("EvalUsageTab search scope (W10-EVALS-U05)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.chart = exact({ stats: {}, chart: [] }, h.refetchChart);
    h.logs = exact(
      { table: PAGE_ONE, pagination: { total: 60 } },
      h.refetchLogs,
    );
  });

  it("labels the search field as covering this page", () => {
    render(<EvalUsageTab templateId="eval-1" />);

    expect(
      screen.getByRole("textbox", { name: "Search this page" }),
    ).toHaveAttribute("placeholder", "Search this page");
  });

  it("says the search missed this page instead of claiming the period has no logs", () => {
    render(<EvalUsageTab templateId="eval-1" />);

    search("shipping address");

    expect(
      screen.getByText('No logs on this page match "shipping address"'),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No evaluation logs for this period"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("usage-pagination")).toHaveTextContent(
      "1-25 of 60",
    );

    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));

    expect(
      screen.getByRole("textbox", { name: "Search this page" }),
    ).toHaveValue("");
    expect(screen.getAllByTestId("usage-row")).toHaveLength(2);
  });

  it("keeps the no-logs message for a period without logs", () => {
    h.logs = exact({ table: [], pagination: { total: 0 } }, h.refetchLogs);
    render(<EvalUsageTab templateId="eval-1" />);

    search("refund");

    expect(screen.getByTestId("usage-table")).toHaveTextContent(
      "No evaluation logs for this period",
    );
    expect(
      screen.queryByRole("button", { name: "Clear search" }),
    ).not.toBeInTheDocument();
  });
});
