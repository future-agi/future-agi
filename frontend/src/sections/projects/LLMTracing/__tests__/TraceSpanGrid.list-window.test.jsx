/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, waitFor, within } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { observePresetDateFilter } from "src/sections/projects/timeWindowPresets";

// An unfiltered Trace/Spans page that is empty only because of its date
// window names the window and the project's last trace, and offers "Show all
// time" (Past 12M, the widest preset) beside the date picker. A project with
// nothing traced keeps the first-run state; chip filters that matched nothing
// keep "Clear filters" and make no read.
//
// Every instant sits at 12:00 UTC, so each calendar date below holds for any
// UTC offset under 12 hours.
const NOW = new Date("2026-11-11T12:00:00Z");
const LAST_ACTIVE = "2026-11-01T12:00:00+00:00";

// The overlay's own glyphs: the empty state's icon and the date pill's.
for (const name of [
  "solar:calendar-mark-linear",
  "solar:inbox-linear",
  "solar:calendar-linear",
  "solar:alt-arrow-down-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

const { getMock, readProjectsMock, gridState } = vi.hoisted(() => ({
  getMock: vi.fn(),
  readProjectsMock: vi.fn(),
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
// Side-effect stylesheet and the two detail drawers (they pull the whole
// trace-detail tree), stubbed as in TraceSpanGrid.empty-overlay.test.jsx.
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("../LLMTracingTraceDetailDrawer", () => ({ default: () => null }));
vi.mock("../LLMTracingSpanDetailDrawer", () => ({ default: () => null }));
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  readQuery: (...args) => getMock(...args),
}));
vi.mock("src/api/project/observe-project-list", async (importOriginal) => ({
  ...(await importOriginal()),
  readObserveProjectPage: (...args) => readProjectsMock(...args),
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
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

import SpanGrid from "../SpanGrid";
import TraceGrid from "../TraceGrid";

const SETUP_DOCS_URL = "https://docs.futureagi.com/docs/observe";
const PROJECT_NAME = "Support bot";

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

// A parsed list_projects page for an exact-name match. Another project shares
// the name and traced an hour ago, so the row must be picked by id.
const projectPage = (row) => ({
  rows: [
    {
      id: "project-2",
      name: PROJECT_NAME,
      last_active: "2026-11-11T11:00:00+00:00",
      last_30_days_vol: 40,
      activity_query_complete: true,
    },
    {
      id: "project-1",
      name: PROJECT_NAME,
      last_30_days_vol: 0,
      activity_query_complete: true,
      ...row,
    },
  ],
  totalRows: 2,
  totalPages: 1,
  metadata: { total_rows: 2, total_pages: 1, page_number: 0, page_size: 20 },
});

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

// The primary list's Past 7D window, read under the fixed clock.
const pastWeek = () => ({
  dateOption: "7D",
  dateFilter: observePresetDateFilter("7D"),
});

const listWindowFor = (dateFilter, setDateFilter = vi.fn()) => ({
  projectId: "project-1",
  projectName: PROJECT_NAME,
  dateFilter,
  setDateFilter,
});

let queryClient;

// Mount the grid, settle one exact empty first page, then render the overlay
// AG Grid would show for it, inside the app's query provider.
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
  const { container } = render(
    <QueryClientProvider client={queryClient}>{overlay}</QueryClientProvider>,
  );
  return within(container);
};

// Any read the overlay started has settled and painted.
const settleReads = async () => {
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
};

describe.each([
  { kind: "trace", noun: "traces", title: "No traces found" },
  { kind: "span", noun: "spans", title: "No spans found" },
])("$kind grid empty page outside the date window", ({ kind, noun, title }) => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    getMock.mockReset();
    readProjectsMock.mockReset();
    gridState.api = null;
    gridState.props = null;
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(() => {
    queryClient.clear();
    vi.useRealTimers();
  });

  it("names the window and the last trace, and Show all time widens to Past 12M", async () => {
    readProjectsMock.mockResolvedValue(
      projectPage({ last_active: LAST_ACTIVE }),
    );
    const setDateFilter = vi.fn();
    const dateFilter = pastWeek();
    const overlay = await renderEmptyOverlay(kind, {
      rangeLabel: "Past 7D",
      onWidenRange: vi.fn(),
      onClearFilters: vi.fn(),
      listWindow: listWindowFor(dateFilter, setDateFilter),
    });

    expect(
      await overlay.findByText(
        `No ${noun} between 04 Nov 2026 and 11 Nov 2026`,
      ),
    ).toBeInTheDocument();
    expect(
      overlay.getByText(
        "This project's last trace was 10 days ago (01 Nov 2026).",
      ),
    ).toBeInTheDocument();

    // One bounded read: this project's list_projects row, by exact name.
    expect(readProjectsMock).toHaveBeenCalledTimes(1);
    const [{ params }] = readProjectsMock.mock.calls[0];
    expect(params).toMatchObject({ page_number: 0, page_size: 20 });
    expect(JSON.parse(params.filters)).toEqual([
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: PROJECT_NAME,
        },
      },
    ]);

    // The existing date picker sits beside the primary action; the setup
    // guide is not offered to a project that has traces.
    expect(
      overlay.getByRole("button", { name: "Past 7D" }),
    ).toBeInTheDocument();
    expect(
      overlay.queryByRole("link", { name: "Set up tracing" }),
    ).not.toBeInTheDocument();

    const showAll = overlay.getByRole("button", { name: "Show all time" });
    // AG Grid's overlay wrapper is pointer-events: none.
    expect(showAll.parentElement.style.pointerEvents).toBe("auto");
    fireEvent.click(showAll);

    expect(setDateFilter).toHaveBeenCalledTimes(1);
    const [update] = setDateFilter.mock.calls[0];
    const next = update(dateFilter);
    expect(next).toEqual({
      dateOption: "12M",
      dateFilter: observePresetDateFilter("12M"),
    });
    expect(new Date(next.dateFilter[0]).getTime()).toBeLessThan(
      new Date(dateFilter.dateFilter[0]).getTime(),
    );
  });

  it("keeps the first-run state when the project has no trace to name", async () => {
    readProjectsMock.mockResolvedValue(projectPage({ last_active: null }));
    const onWidenRange = vi.fn();
    const overlay = await renderEmptyOverlay(kind, {
      rangeLabel: "Past 7D",
      onWidenRange,
      onClearFilters: vi.fn(),
      listWindow: listWindowFor(pastWeek()),
    });
    await settleReads();

    expect(overlay.getByText(title)).toBeInTheDocument();
    expect(
      overlay.getByText(
        `There are no ${noun} in the selected time range (Past 7D). Try a longer range, or check that your app is sending traces to this project.`,
      ),
    ).toBeInTheDocument();
    expect(
      overlay.getByRole("link", { name: "Set up tracing" }),
    ).toHaveAttribute("href", SETUP_DOCS_URL);
    fireEvent.click(overlay.getByRole("button", { name: "Show past 30 days" }));
    expect(onWidenRange).toHaveBeenCalledTimes(1);
    expect(
      overlay.queryByRole("button", { name: "Show all time" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the no-match state, with no read, when chip filters matched nothing", async () => {
    readProjectsMock.mockResolvedValue(
      projectPage({ last_active: LAST_ACTIVE }),
    );
    const onClearFilters = vi.fn();
    const overlay = await renderEmptyOverlay(kind, {
      extraFilters: [chip],
      rangeLabel: "Past 7D",
      onWidenRange: vi.fn(),
      onClearFilters,
      listWindow: listWindowFor(pastWeek()),
    });
    await settleReads();

    expect(overlay.getByText(title)).toBeInTheDocument();
    expect(
      overlay.getByText(
        `No ${noun} match these filters in the selected time range (Past 7D).`,
      ),
    ).toBeInTheDocument();
    fireEvent.click(overlay.getByRole("button", { name: "Clear filters" }));
    expect(onClearFilters).toHaveBeenCalledTimes(1);
    expect(
      overlay.queryByRole("button", { name: "Show all time" }),
    ).not.toBeInTheDocument();
    expect(readProjectsMock).not.toHaveBeenCalled();
  });
});
