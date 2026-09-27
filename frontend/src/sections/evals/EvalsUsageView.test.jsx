import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import EvalsUsageView from "./EvalsUsageView";

const mocks = vi.hoisted(() => ({ post: vi.fn(), grid: null }));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    develop: { eval: { getEvalTemplates: "/model-hub/get-eval-templates" } },
  },
}));
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function MockGrid(props, _ref) {
      mocks.grid = props;
      return null;
    }),
  };
});
vi.mock("./EvalsWrapper", () => {
  const Wrapper = ({ children }) => <>{children}</>;
  Wrapper.propTypes = { children: PropTypes.node };
  return { default: Wrapper };
});
vi.mock("apexcharts", () => ({ default: vi.fn() }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/utils/utils", () => ({ preventHeaderSelection: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/LandingPageCard/LandingPageCard", () => ({
  default: () => null,
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));

const rows = [{ id: "eval-1", eval_template_name: "Example evaluation" }];

async function readRows(request = { startRow: 0, endRow: 10, sortModel: [] }) {
  const params = { request, success: vi.fn(), fail: vi.fn() };
  await act(async () => {
    await mocks.grid.serverSideDatasource.getRows(params);
  });
  expect(params.fail).not.toHaveBeenCalled();
  return params;
}

async function search(value) {
  fireEvent.change(screen.getByPlaceholderText("Search"), {
    target: { value },
  });
  await act(async () => vi.advanceTimersByTimeAsync(500));
}

describe("Eval Usage grid search request contract", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.grid = null;
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({
      data: { status: true, result: { row_data: rows, total_rows: 1 } },
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("sends a blank string on first load and renders a successful empty result", async () => {
    mocks.post.mockResolvedValueOnce({
      data: { status: true, result: { row_data: [], total_rows: 0 } },
    });
    render(<EvalsUsageView />, { wrapper: MemoryRouter });

    const params = await readRows();

    expect(mocks.post).toHaveBeenCalledExactlyOnceWith(
      "/model-hub/get-eval-templates",
      { search_text: "", current_page_index: 0, page_size: 10, sort: [] },
    );
    expect(params.success).toHaveBeenCalledExactlyOnceWith({
      rowData: [],
      rowCount: 0,
    });
    expect(
      screen.getByText("Create, test and manage your evaluations"),
    ).toBeInTheDocument();
  });

  it.each(["", "   "])(
    "trims a search and sends blank string when it is cleared to %j",
    async (clearedSearch) => {
      render(<EvalsUsageView />, { wrapper: MemoryRouter });
      await readRows();

      await search("  example  ");
      await readRows({
        startRow: 10,
        endRow: 20,
        sortModel: [{ colId: "eval_template_name", sort: "asc" }],
      });
      expect(mocks.post).toHaveBeenLastCalledWith(
        "/model-hub/get-eval-templates",
        {
          search_text: "example",
          current_page_index: 1,
          page_size: 10,
          sort: [{ column_id: "eval_template_name", type: "ascending" }],
        },
      );

      await search(clearedSearch);
      const params = await readRows();
      expect(mocks.post).toHaveBeenCalledTimes(3);
      expect(mocks.post).toHaveBeenLastCalledWith(
        "/model-hub/get-eval-templates",
        { search_text: "", current_page_index: 0, page_size: 10, sort: [] },
      );
      expect(params.success).toHaveBeenCalledExactlyOnceWith({
        rowData: rows,
        rowCount: 1,
      });
      expect(screen.getByPlaceholderText("Search")).toBeEnabled();
    },
  );
});
