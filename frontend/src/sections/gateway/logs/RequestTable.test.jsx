import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestTable from "./RequestTable";
import { BUILTIN_COLUMNS, resolveColumns } from "./columns/columnModel";

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

  it("renders exactly the ten default columns in order without a columns prop (AC1/R43)", () => {
    render(<RequestTable {...baseProps} />);
    expect(headerLabels()).toEqual(DEFAULT_LABELS);
    const firstRow = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(within(firstRow).getAllByRole("cell")).toHaveLength(10);
    expect(within(firstRow).getAllByRole("cell")[3]).toHaveTextContent(
      "checkout",
    );
  });

  it("renders a custom metadata column per row with - for missing values and no sort control (AC2/AC7/AC10)", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["tenant"])} />);
    expect(headerLabels()).toEqual([...DEFAULT_LABELS, "tenant"]);
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(within(rows[1]).getAllByRole("cell")[10]).toHaveTextContent("acme");
    expect(within(rows[2]).getAllByRole("cell")[10]).toHaveTextContent("-");
    const tenantHeader = screen.getByRole("columnheader", { name: "tenant" });
    expect(tenantHeader.querySelector("span[role='button']")).toBeNull();
    expect(baseProps.setFilters).not.toHaveBeenCalled();
  });

  it("keeps a custom property named model distinct from the built-in Model and renders script-like values as text (AC9)", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["model"])} />);
    expect(headerLabels()).toEqual([...DEFAULT_LABELS, "model"]);
    const table = within(screen.getByRole("table"));
    expect(table.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(table.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders compact JSON for object values and preserves false/zero (AC7)", () => {
    render(<RequestTable {...baseProps} columns={customColumns(["flags"])} />);
    expect(
      within(screen.getByRole("table")).getByText('{"beta":false,"count":0}'),
    ).toBeInTheDocument();
  });

  it("drives header, rows, skeleton and empty colSpan from the same column list (AC6)", () => {
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
});
