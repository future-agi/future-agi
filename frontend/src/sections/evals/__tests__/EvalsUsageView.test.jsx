/* eslint-disable react/prop-types */
import React, { useState } from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor, fireEvent } from "src/utils/test-utils";

const { postMock, navigateMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
  navigateMock: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => postMock(...args) },
  endpoints: {
    develop: {
      eval: {
        getEvalTemplates: "/model-hub/get-eval-templates",
      },
    },
  },
}));

vi.mock("react-router", async (orig) => ({
  ...(await orig()),
  useNavigate: () => navigateMock,
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (v) => v }));

// LandingPageCard (rendered in the empty state) uses `src/components/image`,
// which wraps react-lazy-load-image-component — that library throws under
// jsdom (it reaches into a browser-only prototype during mount). Stub the
// shared Image component down to a plain <img> so the empty-state landing
// content can render without pulling that crash in.
vi.mock("src/components/image", () => ({
  default: ({ alt, src }) => <img alt={alt} src={src} />,
}));

// EvalsUsageView wires AG Grid with `rowModelType="serverSide"`, passing
// `serverSideDatasource` and `onCellClicked` directly as JSX props (unlike
// LogsTabGrid, which registers the datasource imperatively inside
// onGridReady). This stub mirrors that: it fires `getRows` whenever the
// datasource reference changes (exactly as the real grid's server-side row
// model would on init and on filter/sort changes), renders the resulting
// rows as plain divs, and wires `onCellClicked` so row-click navigation can
// be exercised without needing AG Grid's real rendering pipeline or the
// ApexCharts-based cell renderers (which cannot run under jsdom).
function MockAgGridReact({ serverSideDatasource, onCellClicked }) {
  const [rows, setRows] = useState([]);

  const fetchPage = React.useCallback(
    (sortModel = []) => {
      serverSideDatasource?.getRows({
        request: { startRow: 0, endRow: 10, sortModel },
        api: {
          hideOverlay: vi.fn(),
          showNoRowsOverlay: vi.fn(),
          applyColumnState: vi.fn(),
          getGridOption: () => ({}),
          setGridOption: vi.fn(),
        },
        success: ({ rowData }) => setRows(rowData || []),
        fail: () => setRows([]),
      });
    },
    [serverSideDatasource],
  );

  React.useEffect(() => {
    fetchPage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverSideDatasource]);

  return (
    <div data-testid="ag-grid">
      <button
        type="button"
        data-testid="trigger-sort"
        onClick={() =>
          fetchPage([{ colId: "updated_at", sort: "asc" }])
        }
      >
        sort
      </button>
      {rows.map((row) => (
        <div
          key={row.id}
          data-testid={`eval-row-${row.id}`}
          onClick={() =>
            onCellClicked?.({
              column: { getColId: () => "eval_template_name" },
              data: row,
            })
          }
        >
          {row.eval_template_name}
        </div>
      ))}
    </div>
  );
}

MockAgGridReact.propTypes = {
  serverSideDatasource: PropTypes.object,
  onCellClicked: PropTypes.func,
};

vi.mock("ag-grid-react", () => ({ AgGridReact: MockAgGridReact }));

import EvalsUsageView from "../EvalsUsageView";

function mockTemplatesResponse({
  rows = [],
  total = rows.length,
  maxAxis = 100,
  status = true,
} = {}) {
  postMock.mockResolvedValue({
    data: {
      status,
      result: { row_data: rows, total_rows: total, max_axis: maxAxis },
    },
  });
}

const evalRow = (overrides = {}) => ({
  id: "eval-1",
  eval_template_name: "Correctness",
  average: { avg_graph_data: [] },
  error_rate: [],
  last_30_run: 12,
  updated_at: "2026-08-01T00:00:00.000Z",
  max_axis: 100,
  ...overrides,
});

describe("EvalsUsageView", () => {
  beforeEach(() => {
    postMock.mockReset();
    navigateMock.mockReset();
  });

  it("requests the first page of eval templates with no search text and no sort", async () => {
    mockTemplatesResponse({ rows: [evalRow()] });

    render(<EvalsUsageView />);

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));
    const [url, payload] = postMock.mock.calls[0];
    expect(url).toBe("/model-hub/get-eval-templates");
    expect(payload).toMatchObject({
      search_text: null,
      current_page_index: 0,
      page_size: 10,
      sort: [],
    });
  });

  it("renders eval rows returned by the API", async () => {
    mockTemplatesResponse({
      rows: [evalRow({ id: "eval-1", eval_template_name: "Correctness" })],
    });

    render(<EvalsUsageView />);

    expect(await screen.findByTestId("eval-row-eval-1")).toHaveTextContent(
      "Correctness",
    );
    expect(screen.getByPlaceholderText("Search")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create Evaluation" }),
    ).toBeInTheDocument();
  });

  it("shows the empty-state landing cards when the first page has no data and no search", async () => {
    mockTemplatesResponse({ rows: [], total: 0 });

    render(<EvalsUsageView />);

    expect(
      await screen.findByText("Create, test and manage your evaluations"),
    ).toBeInTheDocument();
    expect(screen.getByText("Create Evaluations")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start creating evaluations" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("ag-grid")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Search")).not.toBeInTheDocument();
  });

  it("re-requests with the typed search text (debounce mocked to pass through)", async () => {
    mockTemplatesResponse({ rows: [evalRow()] });

    render(<EvalsUsageView />);
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));

    mockTemplatesResponse({ rows: [evalRow({ eval_template_name: "Toxicity" })] });
    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "toxi" },
    });

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));
    const [, payload] = postMock.mock.calls.at(-1);
    expect(payload.search_text).toBe("toxi");
  });

  it("maps a column sort to the column_id/type sort payload", async () => {
    mockTemplatesResponse({ rows: [evalRow()] });

    render(<EvalsUsageView />);
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));

    mockTemplatesResponse({ rows: [evalRow()] });
    await userEvent.click(screen.getByTestId("trigger-sort"));

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));
    const [, payload] = postMock.mock.calls.at(-1);
    expect(payload.sort).toEqual([
      { column_id: "updated_at", type: "ascending" },
    ]);
  });

  it("navigates to the eval detail page when a row is clicked", async () => {
    mockTemplatesResponse({
      rows: [evalRow({ id: "eval-42", eval_template_name: "Correctness" })],
    });

    render(<EvalsUsageView />);

    const row = await screen.findByTestId("eval-row-eval-42");
    await userEvent.click(row);

    expect(navigateMock).toHaveBeenCalledWith(
      "/dashboard/evaluations/eval-42",
      { state: { dataset: expect.objectContaining({ id: "eval-42" }) } },
    );
  });

  it("navigates to the evaluators tab when Create Evaluation is clicked", async () => {
    mockTemplatesResponse({ rows: [evalRow()] });

    render(<EvalsUsageView />);
    await screen.findByTestId("eval-row-eval-1");

    await userEvent.click(
      screen.getByRole("button", { name: "Create Evaluation" }),
    );

    expect(navigateMock).toHaveBeenCalledWith("/dashboard/evaluations");
  });

  it("does not crash and stops loading state when the request fails", async () => {
    postMock.mockRejectedValue(new Error("network error"));

    render(<EvalsUsageView />);

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Search")).toBeEnabled(),
    );
    expect(screen.getByTestId("ag-grid")).toBeInTheDocument();
  });
});
