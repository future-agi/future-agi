import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import CompareData from "../CompareData";

const { grid, post, deleteCompare } = vi.hoisted(() => ({
  grid: { props: null },
  post: vi.fn(),
  deleteCompare: vi.fn(),
}));

vi.mock("ag-grid-react", async (importOriginal) => {
  const actual = await importOriginal();
  const { forwardRef } = await import("react");
  return {
    ...actual,
    AgGridReact: forwardRef(function ComparisonGrid(props, _ref) {
      grid.props = props;
      return null;
    }),
  };
});
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post } };
});
vi.mock("src/api/develop/dataset-compare", async (importOriginal) => ({
  ...(await importOriginal()),
  useDeleteCompare: () => ({ mutate: deleteCompare }),
}));
vi.mock(
  "../CompareDatasetDetailDrawer/CompareDatasetDetailDrawer",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

const BASE_ID = "10000000-0000-4000-8000-000000000001";
const OTHER_ID = "10000000-0000-4000-8000-000000000002";
const COMPARE_ID = "20000000-0000-4000-8000-000000000001";
const questions = [
  "What is the capital of Japan?",
  "What is 2 + 2?",
  "Name a primary color.",
  "What is the opposite of hot?",
];
const columnConfig = [
  {
    id: "question-base",
    name: "question",
    data_type: "text",
    origin_type: "others",
    group: { id: "question-base", name: "question" },
  },
  ...["base", "other"].map((dataset) => ({
    id: `answer-${dataset}`,
    name: dataset === "base" ? "Original answers" : "Revised answers",
    data_type: "text",
    origin_type: "others",
    group: { id: "answer-base", name: "answer" },
  })),
];
const rows = questions.map((question, index) => ({
  row_id: `comparison-row-${index}`,
  "question-base": { cell_value: question },
  "answer-base": { cell_value: `Original ${index}` },
  "answer-other": { cell_value: `Revised ${index}` },
}));

function response(table = rows, totalRows = table.length) {
  // The app axios preserves the server's snake_case keys, including cell data.
  return {
    data: {
      status: true,
      result: {
        column_config: columnConfig,
        table,
        metadata: {
          compare_id: COMPARE_ID,
          total_rows: totalRows,
          total_pages: Math.ceil(totalRows / 10),
        },
      },
    },
  };
}

function mountComparison() {
  const api = {
    refreshCells: vi.fn(),
    refreshServerSide: vi.fn(),
    showNoRowsOverlay: vi.fn(),
    hideOverlay: vi.fn(),
  };
  const props = {
    gridApiRef: { current: { api } },
    compareId: { current: null },
    baseColumn: "question",
    selectedDatasetsValues: [BASE_ID, OTHER_ID],
    columns: [],
    selectedColumns: ["question-base", "answer-base"],
    setColumns: vi.fn(),
    setCommonColumn: vi.fn(),
    setDatasetInfo: vi.fn(),
    setCompareColumnDefs: vi.fn(),
  };
  render(<CompareData {...props} />);
  return { ...props, api };
}

async function loadPage(startRow = 0) {
  const params = {
    request: { startRow, endRow: startRow + 10 },
    success: vi.fn(),
    fail: vi.fn(),
  };
  await act(async () => {
    await grid.props.serverSideDatasource.getRows(params);
  });
  return params;
}

beforeEach(() => {
  grid.props = null;
  post.mockReset();
  deleteCompare.mockReset();
  window.localStorage.removeItem("showSummaryExp");
});
afterEach(() => {
  cleanup();
  window.localStorage.removeItem("showSummaryExp");
});

describe("dataset comparison canonical response", () => {
  it("renders real column names and all four matched questions", async () => {
    post.mockResolvedValue(response());
    const props = mountComparison();
    const params = await loadPage();

    expect(post).toHaveBeenCalledWith(
      `/model-hub/datasets/${BASE_ID}/compare-datasets/`,
      {
        dataset_ids: [OTHER_ID],
        base_column_name: "question",
        page_size: 10,
        current_page_index: 0,
        compare_id: null,
      },
    );
    expect(params.fail).not.toHaveBeenCalled();
    expect(params.success).toHaveBeenCalledWith({ rowData: rows, rowCount: 4 });
    expect(props.compareId.current).toBe(COMPARE_ID);
    expect(grid.props.columnDefs.map((column) => column.headerName)).toEqual([
      "question",
      "answer",
    ]);
    const [questionColumn, answerGroup] = grid.props.columnDefs;
    expect(answerGroup.children.map((column) => column.headerName)).toEqual([
      "Original answers",
      "Revised answers",
    ]);
    expect(rows.map((data) => questionColumn.valueGetter({ data }))).toEqual(
      questions,
    );
    expect(answerGroup.children[1].valueGetter({ data: rows[0] })).toBe(
      "Revised 0",
    );
    expect(grid.props.getRowId({ data: rows[0] })).toBe("comparison-row-0");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reuses the returned comparison id for the next page", async () => {
    post.mockResolvedValue(response(rows, 14));
    mountComparison();
    await loadPage();
    await loadPage(10);
    expect(post.mock.lastCall[1]).toEqual({
      dataset_ids: [OTHER_ID],
      base_column_name: "question",
      page_size: 10,
      current_page_index: 1,
      compare_id: COMPARE_ID,
    });
  });

  it("keeps real headers when there are genuinely no matching rows", async () => {
    post.mockResolvedValue(response([]));
    const props = mountComparison();
    const params = await loadPage();
    expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });
    expect(grid.props.columnDefs.map((column) => column.headerName)).toEqual([
      "question",
      "answer",
    ]);
    expect(props.api.showNoRowsOverlay).toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports flattened API failures instead of claiming zero matches and allows retry", async () => {
    post.mockRejectedValueOnce({ statusCode: 400, message: "internal detail" });
    const props = mountComparison();
    const failed = await loadPage();
    expect(failed.fail).toHaveBeenCalledOnce();
    expect(failed.success).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load the comparison. Try again.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("internal detail");
    expect(props.api.showNoRowsOverlay).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(props.api.refreshServerSide).toHaveBeenLastCalledWith({ purge: true });
    post.mockResolvedValue(response());
    const retried = await loadPage();
    expect(retried.success).toHaveBeenCalledWith({ rowData: rows, rowCount: 4 });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not turn an invalid response into a successful empty comparison", async () => {
    post.mockResolvedValue({ data: { status: true, result: {} } });
    mountComparison();
    const params = await loadPage();
    expect(params.fail).toHaveBeenCalledOnce();
    expect(params.success).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
