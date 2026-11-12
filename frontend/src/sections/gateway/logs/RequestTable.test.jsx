import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import RequestTable from "./RequestTable";
import {
  BUILTIN_COLUMNS,
  CUSTOM_COLUMN_TEXT_WIDTH,
  CUSTOM_COLUMN_WIDTH,
  resolveColumns,
} from "./columns/columnModel";

afterEach(() => vi.restoreAllMocks());

const mockState = {
  data: {
    results: [
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
        started_at: "2026-05-21T10:01:00Z",
        cache_hit: true,
        guardrail_triggered: true,
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
        status_code: 500,
        latency_ms: 5,
        cost: "0",
        input_tokens: 1,
        output_tokens: 1,
        total_tokens: 2,
        session_id: "session-2",
        started_at: "2026-05-21T10:02:00Z",
        metadata: null,
      },
    ],
    count: 2,
  },
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
  within(screen.getByRole("table"))
    .getAllByRole("columnheader")
    .map((th) => th.textContent);

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
};

describe("RequestTable", () => {
  it("renders request-log rows using canonical snake_case API fields", () => {
    const onSelectLog = vi.fn();

    render(<RequestTable {...baseProps} onSelectLog={onSelectLog} />);

    const table = within(screen.getByRole("table"));
    expect(table.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(table.getByText("openai")).toBeInTheDocument();
    expect(table.getByText("456ms")).toBeInTheDocument();
    expect(table.getByText("10 / 12")).toBeInTheDocument();
    expect(table.getByText("session-1")).toBeInTheDocument();
    expect(table.getByText("checkout")).toBeInTheDocument();
    expect(table.getByText("recommendations")).toBeInTheDocument();
    expect(table.queryByText("N/A")).not.toBeInTheDocument();

    table.getByText("gpt-4o-mini").closest("tr").click();
    expect(onSelectLog).toHaveBeenCalledWith("log-row-id");
  });

  it("renders exactly the ten default columns in order without a columns prop", () => {
    render(<RequestTable {...baseProps} />);
    expect(headerLabels()).toEqual(DEFAULT_LABELS);
    const firstRow = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(within(firstRow).getAllByRole("cell")).toHaveLength(10);
    expect(within(firstRow).getAllByRole("cell")[3]).toHaveTextContent(
      "checkout",
    );
  });

  it("renders a custom metadata column per row with - for missing values and no sort control", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["tenant"])} />);
    expect(headerLabels()).toEqual([...DEFAULT_LABELS, "tenant"]);
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(within(rows[1]).getAllByRole("cell")[10]).toHaveTextContent("acme");
    expect(within(rows[2]).getAllByRole("cell")[10]).toHaveTextContent("-");
    const tenantHeader = screen.getByRole("columnheader", { name: "tenant" });
    expect(tenantHeader.querySelector("span[role='button']")).toBeNull();
    expect(baseProps.setFilters).not.toHaveBeenCalled();
  });

  it("keeps a custom property named model distinct from the built-in Model and renders script-like values as text", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["model"])} />);
    expect(headerLabels()).toEqual([...DEFAULT_LABELS, "model"]);
    const table = within(screen.getByRole("table"));
    expect(table.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(table.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders compact JSON for object values and preserves false/zero", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["flags"])} />);
    expect(
      within(screen.getByRole("table")).getByText('{"beta":false,"count":0}'),
    ).toBeInTheDocument();
  });

  it("keeps a clipped custom header bounded and reveals its full name by keyboard", async () => {
    const user = userEvent.setup();
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(500);
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(
      CUSTOM_COLUMN_TEXT_WIDTH,
    );
    const name = `${"billing_reconciliation_".repeat(3)}acc`; // 72 characters
    render(<RequestTable {...baseProps} columns={customColumns([name])} />);
    const header = screen.getByRole("columnheader", { name });
    expect(header).toHaveStyle({
      width: `${CUSTOM_COLUMN_WIDTH}px`,
      minWidth: `${CUSTOM_COLUMN_WIDTH}px`,
    });
    const label = within(header).getByText(name);
    expect(label).toHaveAttribute("aria-label", name);
    expect(label).toHaveAttribute("tabindex", "0");
    expect(label).toHaveStyle({
      maxWidth: `${CUSTOM_COLUMN_TEXT_WIDTH}px`,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
    // The label never widens the column past CUSTOM_COLUMN_WIDTH.
    const { paddingLeft, paddingRight } = getComputedStyle(header);
    expect(
      CUSTOM_COLUMN_TEXT_WIDTH +
        parseFloat(paddingLeft) +
        parseFloat(paddingRight),
    ).toBe(CUSTOM_COLUMN_WIDTH);
    for (let i = 0; i < 20 && document.activeElement !== label; i += 1)
      await user.tab();
    expect(label).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(name);
  });

  it("does not add a keyboard stop to a custom header whose label fits", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["tenant"])} />);
    const header = screen.getByRole("columnheader", {
      name: "tenant",
      exact: true,
    });
    expect(within(header).getByText("tenant")).not.toHaveAttribute("tabindex");
  });

  it("drives header, rows, skeleton and empty colSpan from the same column list", () => {
    const columns = customColumns(
      ["tenant"],
      ["builtin:provider", "builtin:cost"],
    );
    expect(columns).toHaveLength(9);

    const { unmount } = render(
      <RequestTable {...baseProps} columns={columns} />,
    );
    let rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(within(rows[0]).getAllByRole("columnheader")).toHaveLength(9);
    expect(within(rows[1]).getAllByRole("cell")).toHaveLength(9);
    expect(headerLabels()).not.toContain("Provider");
    unmount();

    mockState.isLoading = true;
    const loading = render(<RequestTable {...baseProps} columns={columns} />);
    rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(within(rows[1]).getAllByRole("cell")).toHaveLength(9);
    loading.unmount();
    mockState.isLoading = false;

    const savedData = mockState.data;
    mockState.data = { results: [], count: 0 };
    render(<RequestTable {...baseProps} columns={columns} />);
    expect(screen.getByText("No requests found").closest("td")).toHaveAttribute(
      "colspan",
      "9",
    );
    mockState.data = savedData;
  });

  describe("built-in sort headers", () => {
    const sortLabel = (name) =>
      within(screen.getByRole("columnheader", { name })).getByRole("button");

    // tenant sits between Service and Status, ahead of Latency.
    const tenantBeforeLatency = resolveColumns({
      config: {
        v: 1,
        columns: [
          ...BUILTIN_COLUMNS.slice(0, 5).map((c) => ({ id: c.id })),
          { id: "metadata:tenant" },
          ...BUILTIN_COLUMNS.slice(5).map((c) => ({ id: c.id })),
        ],
        hidden: [],
      },
      declarations: [{ name: "tenant" }],
      status: "success",
    }).columns;

    it("shows Timestamp as the active sort by default", () => {
      render(<RequestTable {...baseProps} />);
      expect(sortLabel("Timestamp")).toHaveClass("Mui-active");
      expect(sortLabel("Latency")).not.toHaveClass("Mui-active");
    });

    it.each([
      ["the default columns", undefined],
      ["a custom column placed before it", tenantBeforeLatency],
    ])(
      "sorts by latency descending from page 1 with %s",
      async (_label, columns) => {
        const user = userEvent.setup();
        const setFilters = vi.fn();
        render(
          <RequestTable
            {...baseProps}
            filters={{ model: "gpt-4o-mini" }}
            setFilters={setFilters}
            columns={columns}
          />,
        );

        await user.click(sortLabel("Latency"));

        expect(setFilters).toHaveBeenCalledWith({
          model: "gpt-4o-mini",
          sort: "-latency_ms",
          page: "1",
        });
      },
    );
  });
});
