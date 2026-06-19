/* eslint-disable react/prop-types */
import { describe, expect, it, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import {
  buildAnnotatorFilterChipLabelMap,
  buildReadOnlyColumnDefs,
  buildSimulationSelectorColumnDefs,
  buildSimulationSelectorFilterFields,
  DatasetRowSelector,
  SelectionCheckboxNudge,
} from "../items/add-items-dialog";
import {
  buildSessionSelectAllMeta,
  buildSessionSelectionFilters,
  buildSessionSelectorFilterFields,
  getSessionSelectionRowId,
} from "../items/add-items-session-utils";

const agGridMock = vi.hoisted(() => ({
  api: {
    setGridOption: vi.fn(),
    getGridOption: vi.fn(),
    getDisplayedRowCount: vi.fn(() => 0),
    getLastDisplayedRowIndex: vi.fn(() => -1),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    isDestroyed: vi.fn(() => false),
    getServerSideSelectionState: vi.fn(() => ({ selectAll: false })),
    forEachNode: vi.fn(),
  },
}));

const queryClientMock = vi.hoisted(() => ({
  fetchQuery: vi.fn(async () => ({
    data: { result: { table: [], metadata: { total_rows: 0 } } },
  })),
}));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));

vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const AgGridReact = ({ onGridReady }) => {
    React.useEffect(() => {
      onGridReady?.({ api: agGridMock.api });
    }, [onGridReady]);
    return React.createElement("div", { "data-testid": "dataset-grid" });
  };
  AgGridReact.propTypes = { onGridReady: PropTypes.func };
  return { AgGridReact };
});

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useQueryClient: () => queryClientMock,
    useQuery: (options) => {
      const key = options?.queryKey || [];
      if (key[0] === "datasets-list-simple") {
        return {
          data: [{ dataset_id: "dataset-1", name: "Support Prompts" }],
          isLoading: false,
          isFetching: false,
        };
      }
      if (key[0] === "dataset-detail") {
        return {
          data: {
            data: {
              result: {
                column_config: [
                  {
                    id: "prompt",
                    name: "Prompt",
                    data_type: "text",
                    is_visible: true,
                  },
                ],
              },
            },
          },
          isLoading: false,
          isFetching: false,
        };
      }
      return actual.useQuery(options);
    },
  };
});

function valuesByHeader(row, columnOrder = []) {
  return Object.fromEntries(
    buildSimulationSelectorColumnDefs(columnOrder)
      .filter((column) => column.headerName && column.valueGetter)
      .map((column) => [
        column.headerName,
        column.valueGetter({ data: row, value: undefined }),
      ]),
  );
}

describe("Simulation add-items columns", () => {
  it("renders raw serializer metrics for voice simulation rows", () => {
    const values = valuesByHeader({
      duration_seconds: 44,
      response_time_ms: 1250,
      avg_agent_latency_ms: 2742,
      talk_ratio: 0.17994553981140626,
      cost_cents: 89,
    });

    expect(values.Duration).toBe("44s");
    expect(values["Response Time"]).toBeUndefined();
    expect(values.Latency).toBe("2.74s");
    expect(values["Agent Talk (%)"]).toBe("15.3%");
    expect(values.Cost).toBe("$0.89");
  });

  it("renders nested customer metric aliases used by provider payloads", () => {
    const values = valuesByHeader({
      customer_latency_metrics: {
        systemMetrics: {
          responseTimeMs: 980,
          avgAgentLatencyMs: 1794,
          botPct: 28.2,
        },
      },
      customer_cost_breakdown: {
        total: 0.1234,
      },
    });

    expect(values["Response Time"]).toBeUndefined();
    expect(values.Latency).toBe("1.79s");
    expect(values["Agent Talk (%)"]).toBe("28.2%");
    expect(values.Cost).toBe("$0.1234");
  });

  it("deduplicates visible legacy metric column ids from execution column order", () => {
    const columns = buildSimulationSelectorColumnDefs([
      { id: "avg_agent_latency_ms", column_name: "Average Latency (ms)" },
      { id: "latency_ms", column_name: "Latency (ms)" },
      { id: "customer_cost_cents", column_name: "Customer Cost" },
      { id: "cost", column_name: "Cost" },
      { id: "response_time_ms", column_name: "Response Time (ms)" },
      { id: "avg_response_time_ms", column_name: "Average Response Time" },
    ]);

    expect(
      columns.filter((column) => column.headerName === "Latency"),
    ).toHaveLength(1);
    expect(
      columns.filter((column) => column.headerName === "Cost"),
    ).toHaveLength(1);
  });

  it("hides response-time aliases because voice observability does not show it", () => {
    const columns = buildSimulationSelectorColumnDefs([
      { id: "response_time_ms", column_name: "Response Time (ms)" },
      { id: "avg_response_time_ms", column_name: "Average Response Time" },
      { id: "responseTimeMs", column_name: "Response Time" },
    ]);

    expect(
      columns.filter((column) => column.headerName === "Response Time"),
    ).toHaveLength(0);
    expect(
      columns.filter(
        (column) =>
          column.colId === "response_time" ||
          column.colId === "response_time_ms" ||
          column.colId === "avg_response_time_ms" ||
          column.colId === "responseTimeMs",
      ),
    ).toHaveLength(0);
  });

  it("keeps agent talk blank when no direct value or ratio exists", () => {
    const values = valuesByHeader({});

    expect(values["Agent Talk (%)"]).toBe("-");
  });
});

describe("Simulation add-items filters", () => {
  it("exposes the same core simulation filters used by automation rules", () => {
    expect(buildSimulationSelectorFilterFields()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "status",
          name: "Status",
          category: "system",
          type: "categorical",
        }),
        expect.objectContaining({
          id: "simulation_call_type",
          name: "Simulation Call Type",
          category: "system",
          type: "text",
        }),
        expect.objectContaining({
          id: "persona.language",
          name: "Language",
          category: "persona",
          type: "categorical",
          choices: expect.arrayContaining(["English", "Hindi"]),
        }),
        expect.objectContaining({
          id: "persona.communication_style",
          name: "Communication Style",
          category: "persona",
          type: "categorical",
        }),
        expect.objectContaining({
          id: "persona.multilingual",
          name: "Multilingual",
          category: "persona",
          type: "boolean",
        }),
        expect.objectContaining({
          id: "duration_seconds",
          name: "Duration",
          category: "system",
          type: "number",
        }),
        expect.objectContaining({
          id: "avg_agent_latency_ms",
          name: "Latency",
          category: "system",
          type: "number",
        }),
        expect.objectContaining({
          id: "cost_cents",
          name: "Cost",
          category: "system",
          type: "number",
        }),
        expect.objectContaining({
          id: "created_at",
          name: "Created At",
          category: "system",
          type: "date",
        }),
      ]),
    );
  });

  it("adds scenario attributes and eval columns from simulation column order", () => {
    const fields = buildSimulationSelectorFilterFields([
      {
        id: "scenario-priority",
        column_name: "Priority",
        data_type: "text",
        type: "scenario_dataset_column",
      },
      {
        id: "scenario-attempts",
        column_name: "Attempts",
        data_type: "integer",
        type: "scenario_dataset_column",
      },
      {
        id: "eval-quality",
        column_name: "Quality Score",
        output_type: "score",
        type: "evaluation",
      },
      {
        id: "tool-eval-status",
        column_name: "Tool Status",
        output_type: "Pass/Fail",
        type: "tool_evaluation",
      },
    ]);

    expect(fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "scenario-priority",
          name: "Priority",
          category: "attribute",
          type: "text",
        }),
        expect.objectContaining({
          id: "scenario-attempts",
          name: "Attempts",
          category: "attribute",
          type: "number",
        }),
        expect.objectContaining({
          id: "eval-quality",
          name: "Quality Score",
          category: "eval",
          type: "number",
        }),
        expect.objectContaining({
          id: "tool-eval-status",
          name: "Tool Status",
          category: "eval",
          type: "text",
        }),
      ]),
    );
  });
});

describe("Add-items selection nudge", () => {
  it("shows the checkbox guidance until rows are selected", () => {
    const { rerender } = render(<SelectionCheckboxNudge selectionCount={0} />);

    expect(
      screen.getByText(/use the checkbox column to select rows/i),
    ).toBeInTheDocument();

    rerender(<SelectionCheckboxNudge selectionCount={2} />);

    expect(
      screen.queryByText(/use the checkbox column to select rows/i),
    ).not.toBeInTheDocument();
  });
});

describe("Session add-items filters", () => {
  it("maps session fields to the searchable filter panel shape", () => {
    const fields = buildSessionSelectorFilterFields([
      {
        id: "annotation_quality",
        name: "Annotation Quality",
        groupBy: "Annotation Metrics",
        dataType: "number",
      },
    ]);

    expect(fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "session_id",
          name: "Session ID",
          category: "system",
          type: "string",
        }),
        expect.objectContaining({
          id: "start_time",
          name: "Start Time",
          category: "system",
          type: "datetime",
        }),
        expect.objectContaining({
          id: "annotation_quality",
          name: "Annotation Quality",
          category: "annotation",
          type: "number",
        }),
      ]),
    );
  });

  it("adds the date-range filter in the API payload shape used by list sessions", () => {
    const filters = buildSessionSelectionFilters(
      [
        {
          column_id: "total_traces_count",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: "2",
          },
        },
      ],
      { dateFilter: ["2026-01-01", "2026-02-01"] },
    );

    expect(filters).toEqual([
      {
        column_id: "total_traces_count",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: "2",
        },
      },
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            "2026-01-01T00:00:00.000Z",
            "2026-02-01T00:00:00.000Z",
          ],
        },
      },
    ]);
  });

  it("builds select-all metadata from backend session_id rows", () => {
    const api = {
      getServerSideSelectionState: () => ({
        selectAll: true,
        toggledNodes: ["session-2"],
      }),
      getGridOption: () => ({ totalRowCount: 4 }),
      getRenderedNodes: () => [
        { data: { session_id: "session-1" } },
        { data: { session_id: "session-2" } },
        { data: { session_id: "session-3" } },
      ],
    };

    const meta = buildSessionSelectAllMeta(api);

    expect(meta.totalCount).toBe(4);
    expect(meta.visibleCount).toBe(2);
    expect(meta.visibleRowIds).toEqual(["session-1", "session-3"]);
    expect([...meta.excludedIds]).toEqual(["session-2"]);
  });

  it("keeps session row id extraction on the backend contract key first", () => {
    expect(
      getSessionSelectionRowId({
        id: "node-fallback",
        data: {
          session_id: "backend-session",
          sessionId: "camel-session",
          id: "row-id",
        },
      }),
    ).toBe("backend-session");
  });
});

describe("Dataset read-only column defs", () => {
  it("builds columns from the snake_case dataset config the API returns", () => {
    const columns = buildReadOnlyColumnDefs([
      {
        id: "prompt",
        name: "Prompt",
        data_type: "text",
        is_frozen: true,
        is_visible: true,
      },
    ]);

    expect(columns).toHaveLength(1);
    expect(columns[0]).toMatchObject({
      field: "prompt",
      headerName: "Prompt",
      dataType: "text",
      pinned: true,
      hide: false,
    });
    expect(columns[0].col.dataType).toBe("text");
  });

  it("hides only columns explicitly marked is_visible: false", () => {
    const columns = buildReadOnlyColumnDefs([
      { id: "a", name: "A", data_type: "text", is_visible: false },
      { id: "b", name: "B", data_type: "text" },
      { id: "c", name: "C", data_type: "text", is_visible: true },
    ]);

    expect(columns.map((col) => col.field)).toEqual(["b", "c"]);
    expect(columns.every((col) => col.hide === false)).toBe(true);
  });

  it("reads cell values off the snake_case cell_value key", () => {
    const [column] = buildReadOnlyColumnDefs([
      { id: "prompt", name: "Prompt", data_type: "text", is_visible: true },
    ]);

    const value = column.valueGetter({
      data: { prompt: { cell_value: "hello" } },
    });

    expect(value).toBe("hello");
  });
});

describe("Dataset add-items search", () => {
  beforeEach(() => {
    agGridMock.api.setGridOption.mockClear();
    agGridMock.api.getGridOption.mockClear();
    agGridMock.api.getDisplayedRowCount.mockClear();
    agGridMock.api.getLastDisplayedRowIndex.mockClear();
    agGridMock.api.addEventListener.mockClear();
    agGridMock.api.removeEventListener.mockClear();
    agGridMock.api.isDestroyed.mockClear();
    agGridMock.api.getServerSideSelectionState.mockClear();
    agGridMock.api.forEachNode.mockClear();
    queryClientMock.fetchQuery.mockClear();
  });

  it("refreshes dataset rows as search text changes without waiting for Enter", async () => {
    const user = userEvent.setup();
    render(
      <DatasetRowSelector onSetSelection={vi.fn()} onSelectAll={vi.fn()} />,
    );

    await user.click(screen.getByRole("combobox", { name: /dataset/i }));
    await user.click(screen.getByRole("option", { name: "Support Prompts" }));

    const searchInput =
      await screen.findByPlaceholderText(/search in dataset/i);
    agGridMock.api.setGridOption.mockClear();
    await user.type(searchInput, "refund");

    await waitFor(() => {
      expect(agGridMock.api.setGridOption).toHaveBeenCalledWith(
        "serverSideDatasource",
        expect.any(Object),
      );
    });

    const dataSource =
      agGridMock.api.setGridOption.mock.calls.at(-1)?.[1] ?? null;
    expect(dataSource).toEqual(
      expect.objectContaining({ getRows: expect.any(Function) }),
    );

    const success = vi.fn();
    const fail = vi.fn();
    await dataSource.getRows({
      request: { startRow: 0, sortModel: [] },
      api: agGridMock.api,
      success,
      fail,
    });

    const queryOptions = queryClientMock.fetchQuery.mock.calls.at(-1)?.[0];
    expect(queryOptions?.queryKey?.[5]).toBe("refund");
    expect(success).toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("Add-items annotator filter chips", () => {
  it("maps selected annotator ids to name and email labels", () => {
    expect(
      buildAnnotatorFilterChipLabelMap([
        {
          value: "e1f8e455-9248-4aec-a510-ead35a946235",
          label: "Kartik",
          email: "kartik.nvj@futureagi.com",
        },
      ]),
    ).toEqual({
      annotator: {
        "e1f8e455-9248-4aec-a510-ead35a946235":
          "Kartik (kartik.nvj@futureagi.com)",
      },
    });
  });
});
