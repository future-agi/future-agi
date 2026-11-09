import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import RequestTable from "./RequestTable";
import { grid } from "./__tests__/fakeAgGrid";
import { BUILTIN_COLUMNS, resolveColumns } from "./columns/columnModel";

vi.mock("ag-grid-react", () => import("./__tests__/fakeAgGrid"));

afterEach(() => vi.restoreAllMocks());

const ROWS = [
  {
    id: "log-row-id",
    request_id: "request-1",
    model: "gpt-4o-mini",
    provider: "openai",
    status_code: 200,
    latency_ms: 456,
    cost: "0.001000",
    input_tokens: 10,
    output_tokens: 12,
    total_tokens: 22,
    session_id: "session-1",
    started_at: new Date(2026, 4, 21, 10, 1, 0, 42).toISOString(),
    cache_hit: true,
    guardrail_triggered: false,
    fallback_used: true,
    metadata: {
      application: "checkout",
      service: "recommendations",
      tenant: "acme",
      model: "<img src=x onerror=alert(1)>",
      flags: { beta: false, count: 0 },
    },
  },
  {
    id: "log-row-2",
    request_id: "request-2",
    model: "claude",
    provider: "anthropic",
    status_code: 403,
    guardrail_triggered: true,
    latency_ms: 5,
    cost: "0",
    input_tokens: 1,
    output_tokens: 1,
    total_tokens: 2,
    session_id: "",
    started_at: "2026-05-21T10:02:00Z",
    metadata: null,
  },
];

const mockState = {
  data: { results: ROWS, count: 2 },
  isLoading: false,
  error: null,
  refetch: vi.fn(),
};

vi.mock("./hooks/useRequestLogs", () => ({
  default: () => mockState,
}));

const DEFAULT_LABELS = [
  "Timestamp",
  "Model",
  "Provider",
  "Application",
  "Service",
  "Status",
  "Latency",
  "Cost",
  "Tokens",
  "Session ID",
];

const headerLabels = () =>
  within(screen.getByTestId("ag-grid"))
    .getAllByRole("columnheader")
    .map((th) => th.textContent);

const rows = () =>
  within(screen.getByTestId("ag-grid")).getAllByRole("row").slice(1);

const customColumns = (names, hidden = []) =>
  resolveColumns({
    config: {
      v: 1,
      columns: [
        ...BUILTIN_COLUMNS.map((c) => ({ id: c.id })),
        ...names.map((n) => ({ id: `metadata:${n}` })),
      ],
      hidden,
    },
    declarations: names.map((name) => ({ name })),
    status: "success",
  }).columns;

const baseProps = {
  filters: {},
  setFilter: vi.fn(),
  setFilters: vi.fn(),
  onSelectLog: vi.fn(),
  onClearFilters: vi.fn(),
};

describe("RequestTable on the gateway grid", () => {
  it("renders dense rows from the canonical snake_case fields", () => {
    render(<RequestTable {...baseProps} />);
    const first = within(rows()[0]);
    expect(first.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(first.getByText("openai")).toBeInTheDocument();
    expect(first.getByText("456 ms")).toBeInTheDocument();
    expect(first.getByText("10 / 12")).toBeInTheDocument();
    expect(first.getByText("session-1")).toBeInTheDocument();
    expect(first.getByText("checkout")).toBeInTheDocument();
    expect(first.getByText("recommendations")).toBeInTheDocument();
    // Times to the millisecond, as a log table reads.
    expect(first.getByText(/May 21 10:01:00\.042/)).toBeInTheDocument();
    expect(grid.props.rowHeight).toBe(32);
    expect(grid.props.getRowId({ data: ROWS[0] })).toBe("log-row-id");
  });

  // An own-key request with no price is unknown, not free.
  it("an unpriced request reads an em dash with No price found, never $0.00", () => {
    const saved = mockState.data;
    mockState.data = {
      results: [
        {
          ...ROWS[0],
          id: "unpriced",
          cost: "0",
          cost_source: "unpriced",
          billing_mode: "byok",
        },
        { ...ROWS[1], id: "free", cost: "0", cost_source: "catalog" },
      ],
      count: 2,
    };
    try {
      render(<RequestTable {...baseProps} />);
      const [unpriced, free] = rows();
      const at = headerLabels().indexOf("Cost");
      const cost = (row) => row.querySelectorAll("td")[at];
      expect(cost(unpriced)).toHaveTextContent(/^—$/);
      expect(within(cost(unpriced)).getByTitle("No price found")).toBeVisible();
      expect(cost(free)).toHaveTextContent("$0.00");
    } finally {
      mockState.data = saved;
    }
  });

  // A managed request's provider reads
  // "Future AGI managed", never the upstream it was bought from. The label
  // follows the billing mode: an organization's own OpenRouter key keeps its
  // name.
  it("a managed log reads Future AGI managed in the provider cell; an own-key log keeps its provider", () => {
    const saved = mockState.data;
    mockState.data = {
      results: [
        {
          ...ROWS[0],
          id: "managed",
          provider: "openrouter",
          billing_mode: "managed",
        },
        { ...ROWS[1], id: "own", provider: "openrouter", billing_mode: "byok" },
      ],
      count: 2,
    };
    try {
      render(<RequestTable {...baseProps} />);
      const [managed, own] = rows();
      expect(
        within(managed).getByText("Future AGI"),
      ).toBeInTheDocument();
      expect(within(managed).queryByText("openrouter")).toBeNull();
      expect(within(own).getByText("openrouter")).toBeInTheDocument();
    } finally {
      mockState.data = saved;
    }
  });

  // The header menu button is
  // hidden, but a right-click on a header still opens AG Grid's column menu,
  // whose Choose Columns would hide columns behind the Logs Columns picker
  // and its saved views. The menu keeps everything else.
  it("leaves choosing columns to the Columns picker: the header menu has no Choose Columns", () => {
    render(<RequestTable {...baseProps} />);
    const defaultItems = [
      "pinSubMenu",
      "separator",
      "autoSizeThis",
      "autoSizeAll",
      "separator",
      "columnChooser",
      "resetColumns",
    ];
    expect(grid.props.getMainMenuItems).toEqual(expect.any(Function));
    expect(grid.props.getMainMenuItems({ defaultItems })).toEqual([
      "pinSubMenu",
      "separator",
      "autoSizeThis",
      "autoSizeAll",
      "separator",
      "resetColumns",
    ]);
  });

  it("renders exactly the ten default columns in order", () => {
    render(<RequestTable {...baseProps} />);
    expect(headerLabels()).toEqual(DEFAULT_LABELS);
    expect(within(rows()[0]).getAllByRole("cell")).toHaveLength(10);
  });

  it("opens the drawer for a row, but not for a link inside it", async () => {
    const user = userEvent.setup();
    const onSelectLog = vi.fn();
    render(<RequestTable {...baseProps} onSelectLog={onSelectLog} />);
    await user.click(within(rows()[0]).getByText("gpt-4o-mini"));
    expect(onSelectLog).toHaveBeenCalledWith("log-row-id");

    onSelectLog.mockClear();
    grid.props.onRowClicked({
      data: ROWS[0],
      event: { target: document.createElement("a") },
    });
    expect(onSelectLog).not.toHaveBeenCalled();
  });

  it("marks the status by outcome, and a 403 as a guardrail block only when one triggered", () => {
    render(<RequestTable {...baseProps} />);
    const [ok, blocked] = rows();
    expect(within(ok).getByText("200")).toBeInTheDocument();
    expect(within(ok).getByLabelText("Served from cache")).toBeInTheDocument();
    expect(within(ok).getByLabelText("Fallback used")).toBeInTheDocument();
    expect(within(blocked).getByText("403")).toBeInTheDocument();
    expect(
      within(blocked).getByLabelText("Blocked by a guardrail"),
    ).toBeInTheDocument();
  });

  // Every row carried the same green 200 chip.
  it("a 200 row renders no chip; a 4xx or 5xx keeps one", () => {
    render(<RequestTable {...baseProps} />);
    const [ok, blocked] = rows();
    expect(ok.querySelector(".MuiChip-root")).toBeNull();
    expect(within(ok).getByText("200")).toHaveStyle({
      color: palette("light").text.secondary,
    });
    expect(blocked.querySelector(".MuiChip-root")).not.toBeNull();
  });

  it("renders a custom metadata column with — for missing values and keeps values as text", () => {
    render(
      <RequestTable
        {...baseProps}
        columns={customColumns(["tenant", "model"])}
      />,
    );
    expect(headerLabels()).toEqual([...DEFAULT_LABELS, "tenant", "model"]);
    expect(within(rows()[0]).getAllByRole("cell")[10]).toHaveTextContent(
      "acme",
    );
    expect(within(rows()[1]).getAllByRole("cell")[10]).toHaveTextContent("—");
    expect(
      screen.getByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    // Custom columns cannot be sorted by the server.
    expect(screen.queryByRole("button", { name: "Sort by tenant" })).toBeNull();
  });

  it("sorts on the server: only the five API fields sort, never in the page", async () => {
    const user = userEvent.setup();
    const setFilters = vi.fn();
    render(
      <RequestTable
        {...baseProps}
        filters={{ model: "gpt-4o-mini", page: "3" }}
        setFilters={setFilters}
      />,
    );
    const sortable = grid.props.columnDefs
      .filter((c) => c.sortable)
      .map((c) => c.headerName);
    expect(sortable).toEqual([
      "Timestamp",
      "Status",
      "Latency",
      "Cost",
      "Tokens",
    ]);
    const time = grid.props.columnDefs.find(
      (c) => c.headerName === "Timestamp",
    );
    expect(time.sort).toBe("desc");
    expect(time.comparator(1, 2)).toBe(0);

    await user.click(screen.getByRole("button", { name: "Sort by Latency" }));
    expect(setFilters).toHaveBeenCalledWith({
      model: "gpt-4o-mini",
      sort: "-latency_ms",
      page: "1",
    });
  });

  it("shows the active sort from the URL", () => {
    render(<RequestTable {...baseProps} filters={{ sort: "cost" }} />);
    const byName = (n) => grid.props.columnDefs.find((c) => c.headerName === n);
    expect(byName("Cost").sort).toBe("asc");
    expect(byName("Timestamp").sort).toBeUndefined();
  });

  it("shows one empty state, and a no-results state with a way out when filtered", async () => {
    const user = userEvent.setup();
    const saved = mockState.data;
    mockState.data = { results: [], count: 0 };
    const { unmount } = render(<RequestTable {...baseProps} />);
    expect(screen.getByText("No requests yet")).toBeInTheDocument();
    unmount();

    const onClearFilters = vi.fn();
    render(
      <RequestTable
        {...baseProps}
        filters={{ session_id: "nope", sessionId: "nope" }}
        onClearFilters={onClearFilters}
      />,
    );
    expect(screen.getByText("No matching requests")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onClearFilters).toHaveBeenCalled();
    mockState.data = saved;
  });

  it("shows a calm error with Retry when the first page fails", async () => {
    const user = userEvent.setup();
    const saved = mockState.data;
    mockState.data = undefined;
    mockState.error = { message: "Gateway API unreachable" };
    render(<RequestTable {...baseProps} />);
    expect(
      screen.getByText("Request logs could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.getByText("Gateway API unreachable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(mockState.refetch).toHaveBeenCalled();
    mockState.data = saved;
    mockState.error = null;
  });

  it("passes loading to the grid while the first page loads", () => {
    const saved = mockState.data;
    mockState.data = undefined;
    mockState.isLoading = true;
    render(<RequestTable {...baseProps} />);
    expect(grid.props.loading).toBe(true);
    expect(grid.props.rowData).toBeUndefined();
    mockState.data = saved;
    mockState.isLoading = false;
  });

  it("pages on the server", async () => {
    const user = userEvent.setup();
    const setFilter = vi.fn();
    const saved = mockState.data;
    mockState.data = { results: ROWS, count: 120 };
    render(<RequestTable {...baseProps} setFilter={setFilter} />);
    expect(screen.getByText("1–25 of 120")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /next page/i }));
    expect(setFilter).toHaveBeenCalledWith("page", "2");
    mockState.data = saved;
  });
});
