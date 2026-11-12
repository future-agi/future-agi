/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, waitFor, within } from "src/utils/test-utils";

// OBS-SW-115: an exact empty Trace/Spans page names the time range it read and
// offers one next step (clear the filters, or a longer range and setup).

const { getMock, gridState } = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { api: null, props: null },
}));

vi.mock("ag-grid-react", async (importOriginal) => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, ref) {
      gridState.props = props;
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          get api() {
            return gridState.api;
          },
        }),
        [],
      );
      return <div data-testid="list-grid" />;
    },
  );
  return { ...(await importOriginal()), AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  readQuery: (...args) => getMock(...args),
}));
vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => ({
  ...(await importOriginal()),
  useUrlState: () => ["day", vi.fn(), vi.fn()],
}));
vi.mock("src/routes/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "viewer" }),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));
vi.mock(
  "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover",
  () => ({ default: () => null }),
);
vi.mock("../LLMTracingTraceDetailDrawer", () => ({ default: () => null }));
vi.mock("../LLMTracingSpanDetailDrawer", () => ({ default: () => null }));

import SpanGrid from "../SpanGrid";
import TraceGrid from "../TraceGrid";

const SETUP_DOCS_URL = "https://docs.futureagi.com/docs/observe";

const emptyListResponse = () => ({
  data: {
    status: true,
    result: {
      config: [],
      table: [],
      metadata: {
        has_more: false,
        next_cursor: null,
        total_rows: 0,
        total_rows_is_lower_bound: false,
      },
      query_complete: true,
      query_status: "complete",
    },
  },
});

// Grid API stub: the members the first-page path reads, and a vi.fn() for
// anything else it touches.
const catchAllApi = (members) =>
  new Proxy(members, {
    get(target, key) {
      if (key in target) return target[key];
      if (typeof key === "symbol" || key === "then") return undefined;
      target[key] = vi.fn();
      return target[key];
    },
  });

const makeParams = () => {
  let renderedNodes = [];
  return {
    request: { startRow: 0, endRow: 25, sortModel: [] },
    api: catchAllApi({
      getGui: vi.fn(() => ({
        querySelectorAll: vi.fn(() => [
          { getAttribute: vi.fn(() => "0"), textContent: "page-1" },
        ]),
      })),
      getRenderedNodes: vi.fn(() => renderedNodes),
      paginationGetCurrentPage: vi.fn(() => 0),
    }),
    success: vi.fn(({ rowData = [] }) => {
      renderedNodes = rowData.map((data, index) => ({
        data,
        id: String(index),
      }));
    }),
    fail: vi.fn(),
  };
};

const chip = {
  column_id: "status",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
};

const baseProps = () => ({
  columns: [],
  filters: [{ column_id: "created_at" }],
  extraFilters: [],
  metricFilters: [],
  hasEvalFilter: false,
  cellHeight: "Short",
  compareType: "primary",
  setColumns: vi.fn(),
  setExtraFilters: vi.fn(),
  setFilterOpen: vi.fn(),
  setFilters: vi.fn(),
  setLoading: vi.fn(),
});

// Mount the grid, settle one exact empty first page, then render the overlay
// AG Grid would show for it.
const renderEmptyOverlay = async (kind, props) => {
  getMock.mockResolvedValueOnce(emptyListResponse());
  const ref = React.createRef();
  if (kind === "trace") {
    render(
      <TraceGrid ref={ref} {...baseProps()} {...props} projectId="project-1" />,
    );
  } else {
    render(<SpanGrid ref={ref} {...baseProps()} {...props} />);
  }
  await waitFor(() => expect(gridState.props).not.toBeNull());

  const params = makeParams();
  gridState.api = params.api;
  await act(async () => {
    await gridState.props.serverSideDatasource.getRows(params);
  });
  await waitFor(() => expect(gridState.props.loading).toBe(false));
  expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 });

  const overlay = gridState.props.noRowsOverlayComponent();
  expect(overlay).not.toBeNull();
  const { container } = render(overlay);
  return within(container);
};

describe.each([
  { kind: "trace", noun: "traces", title: "No traces found" },
  { kind: "span", noun: "spans", title: "No spans found" },
])("$kind grid empty overlay", ({ kind, noun, title }) => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.api = null;
    gridState.props = null;
  });

  it("names the range and offers a longer range and setup when nothing matched", async () => {
    const onWidenRange = vi.fn();
    const onClearFilters = vi.fn();
    const overlay = await renderEmptyOverlay(kind, {
      rangeLabel: "Past 7D",
      onWidenRange,
      onClearFilters,
    });

    expect(overlay.getByText(title)).toBeInTheDocument();
    expect(
      overlay.getByText(
        `There are no ${noun} in the selected time range (Past 7D). Try a longer range, or check that your app is sending traces to this project.`,
      ),
    ).toBeInTheDocument();

    const widen = overlay.getByRole("button", { name: "Show past 30 days" });
    // AG Grid's overlay wrapper is pointer-events: none.
    expect(widen.parentElement.style.pointerEvents).toBe("auto");
    fireEvent.click(widen);
    expect(onWidenRange).toHaveBeenCalledTimes(1);

    const setup = overlay.getByRole("link", { name: "Set up tracing" });
    expect(setup).toHaveAttribute("href", SETUP_DOCS_URL);
    expect(setup).toHaveAttribute("target", "_blank");
    expect(
      overlay.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();
    expect(onClearFilters).not.toHaveBeenCalled();
  });

  it("offers Clear filters when the chip filters matched nothing", async () => {
    const onWidenRange = vi.fn();
    const onClearFilters = vi.fn();
    const overlay = await renderEmptyOverlay(kind, {
      extraFilters: [chip],
      rangeLabel: "Past 7D",
      onWidenRange,
      onClearFilters,
    });

    expect(overlay.getByText(title)).toBeInTheDocument();
    expect(
      overlay.getByText(
        `No ${noun} match these filters in the selected time range (Past 7D).`,
      ),
    ).toBeInTheDocument();

    const clear = overlay.getByRole("button", { name: "Clear filters" });
    expect(clear.parentElement.style.pointerEvents).toBe("auto");
    fireEvent.click(clear);
    expect(onClearFilters).toHaveBeenCalledTimes(1);
    expect(
      overlay.queryByRole("button", { name: "Show past 30 days" }),
    ).not.toBeInTheDocument();
    expect(onWidenRange).not.toHaveBeenCalled();
  });

  it("does not offer a longer range when the caller has none to give", async () => {
    const overlay = await renderEmptyOverlay(kind, {
      rangeLabel: "Past 6M",
      onClearFilters: vi.fn(),
    });

    expect(
      overlay.getByText(
        `There are no ${noun} in the selected time range (Past 6M). Try a longer range, or check that your app is sending traces to this project.`,
      ),
    ).toBeInTheDocument();
    expect(
      overlay.queryByRole("button", { name: "Show past 30 days" }),
    ).not.toBeInTheDocument();
    expect(
      overlay.getByRole("link", { name: "Set up tracing" }),
    ).toBeInTheDocument();
  });

  it("keeps the bare title where no range is passed (compare grids)", async () => {
    const overlay = await renderEmptyOverlay(kind, {});

    expect(overlay.getByText(title)).toBeInTheDocument();
    expect(overlay.queryByRole("button")).not.toBeInTheDocument();
    expect(overlay.queryByRole("link")).not.toBeInTheDocument();
  });
});
