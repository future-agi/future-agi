/* eslint-disable react/prop-types */
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => getMock(...args),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalsLogs: "/eval/logs/",
        getEvalLogs: "/eval/log/",
      },
    },
  },
}));

vi.mock("react-router", async (orig) => ({
  ...(await orig()),
  useParams: () => ({ evalId: "eval-1" }),
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (v) => v }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Admin" }) }));

vi.mock("src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider", () => ({
  default: ({ children }) => <>{children}</>,
}));

vi.mock("../../../DevelopFilters/DevelopFilterBox", () => ({
  default: () => null,
}));

vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

// The real cell renderers pull in image/audio viewer contexts, markdown,
// tooltips etc. that are irrelevant to LogsTabGrid's own row/column wiring —
// stub them down to the raw value so tests can assert on rendered text.
vi.mock("../CellRenderingData", () => ({
  CustomCellRender: ({ value }) => (
    <span>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "")}</span>
  ),
  CustomDevelopDetailColumn: ({ displayName }) => <span>{displayName}</span>,
}));

vi.mock("../LogsDrawer", () => ({
  default: ({ open, selectedRow }) =>
    open ? (
      <div data-testid="logs-drawer">{selectedRow?.rowId}</div>
    ) : null,
}));

vi.mock("src/components/TableFilterOptions/TableFilterOptions", () => ({
  default: ({ setSearchQuery }) => (
    <input
      placeholder="Search logs"
      onChange={(e) => setSearchQuery(e.target.value)}
    />
  ),
}));

// AG Grid's real server-side row model can't run in jsdom. LogsTabGrid never
// passes `serverSideDatasource` as a JSX prop — it registers the datasource
// imperatively inside `onGridReady` via `api.setGridOption(...)`, exactly
// like the real grid does on init. This stub captures that registration and
// issues one `getRows` call to render the resulting rows through the real
// column defs (valueGetter + cellRenderer) LogsTabGrid built.
function MockAgGridReact({
  onGridReady,
  columnDefs,
  onCellClicked,
  noRowsOverlayComponent: NoRowsOverlay,
}) {
  const [rows, setRows] = useState([]);

  // Real AG Grid fires `onGridReady` exactly once, on grid init — it is not
  // re-invoked when the prop reference changes on later renders. Mirror that
  // (empty deps) rather than the (incorrect) "search auto-refetches" behavior
  // that re-running this on every render would imply.
  useEffect(() => {
    let dataSource = null;
    const api = {
      setGridOption: (key, value) => {
        if (key === "serverSideDatasource") dataSource = value;
      },
      showNoRowsOverlay: vi.fn(),
      hideOverlay: vi.fn(),
    };
    onGridReady({ api });
    dataSource?.getRows({
      request: { startRow: 0, endRow: 25, sortModel: [] },
      api,
      success: ({ rowData }) => setRows(rowData || []),
      fail: () => setRows([]),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!rows.length) {
    return NoRowsOverlay ? <NoRowsOverlay /> : <div data-testid="no-rows">No rows</div>;
  }

  return (
    <div data-testid="logs-grid">
      {rows.map((row) => (
        <div
          key={row.rowId}
          data-testid={`log-row-${row.rowId}`}
          onClick={() =>
            onCellClicked?.({
              column: { getColId: () => "value" },
              node: { isSelected: () => false, setSelected: vi.fn() },
              data: row,
            })
          }
        >
          {columnDefs
            .filter((col) => !col.hide && col.field !== "checkbox")
            .map((col) => {
              const value = col.valueGetter
                ? col.valueGetter({ data: row })
                : row[col.field];
              const Renderer = col.cellRenderer;
              return Renderer ? (
                <Renderer
                  key={col.field}
                  value={value}
                  data={row}
                  column={{ colDef: col, colId: col.field, getColId: () => col.field }}
                />
              ) : (
                <span key={col.field}>{String(value ?? "")}</span>
              );
            })}
        </div>
      ))}
    </div>
  );
}
MockAgGridReact.propTypes = {
  onGridReady: PropTypes.func,
  columnDefs: PropTypes.array,
  onCellClicked: PropTypes.func,
  noRowsOverlayComponent: PropTypes.elementType,
};

vi.mock("ag-grid-react", () => ({ AgGridReact: MockAgGridReact }));

import LogsTabGrid from "../LogsTabGrid";

function renderGrid(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(
    <Wrapper>
      <LogsTabGrid {...props} />
    </Wrapper>,
  );
}

const columnConfig = [
  { id: "input", name: "Input", data_type: "text", origin_type: "dataset", is_visible: true },
  {
    id: "eval1",
    name: "Correctness",
    data_type: "float",
    origin_type: "evaluation",
    output_type: "score",
    is_visible: true,
  },
];

// The grid reads each page through readEvalLogGridPage, which rejects any
// page that is not provably exact and complete: it needs the full pagination
// metadata, and every row needs both a row id and a log id. `PAGE_SIZE`
// mirrors the block size MockAgGridReact requests below.
const PAGE_SIZE = 25;

function mockLogsResponse(table, total = table.length) {
  getMock.mockResolvedValue({
    data: {
      result: {
        column_config: columnConfig,
        table,
        metadata: {
          total_rows: total,
          total_pages: Math.ceil(total / PAGE_SIZE),
          current_page_index: 0,
          page_size: PAGE_SIZE,
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    },
  });
}

describe("LogsTabGrid", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("renders log rows built from the backend column config + cell values", async () => {
    mockLogsResponse([
      {
        rowId: "r1",
        logId: "log-r1",
        input: { cell_value: "What is 2+2?" },
        eval1: { cell_value: "4" },
      },
    ]);

    renderGrid();

    const row = await screen.findByTestId("log-row-r1");
    expect(row).toHaveTextContent("What is 2+2?");
    expect(row).toHaveTextContent("4");
  });

  it("shows the no-evaluations overlay when there is no log data", async () => {
    mockLogsResponse([], 0);

    renderGrid();

    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(
      await screen.findByText("No evaluations has been logged"),
    ).toBeInTheDocument();
  });

  it("opens the logs drawer with the clicked row", async () => {
    mockLogsResponse([
      { rowId: "r1", logId: "log-r1", input: { cell_value: "hello" }, eval1: {} },
    ]);

    renderGrid();

    const row = await screen.findByTestId("log-row-r1");
    await userEvent.click(row);

    expect(await screen.findByTestId("logs-drawer")).toHaveTextContent("r1");
  });

  it("requests the eval_playground source and shows the Playground Logs title", async () => {
    mockLogsResponse([
      { rowId: "r1", logId: "log-r1", input: { cell_value: "hello" }, eval1: {} },
    ]);

    renderGrid({ isEvalPlayGround: true });

    await screen.findByTestId("log-row-r1");
    expect(screen.getByText("Playground Logs")).toBeInTheDocument();
    const [, config] = getMock.mock.calls.at(-1);
    expect(config.params.source).toBe("eval_playground");
  });
});
