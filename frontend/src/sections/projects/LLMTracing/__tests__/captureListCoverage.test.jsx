import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { parseTraceObserveListResponse, parseSessionObserveListResponse } from "src/api/project/observe-contracts";
import CostCell from "../Renderers/CostCell";
import TokenCell from "../Renderers/TokenCell";
import SessionCellRenderer from "../../SessionsView/SessionCellRenderer";
import { boundObserveListRow } from "../observeListPayload";
import { getTraceListColumnDefs } from "../common";
import CustomTraceRenderer from "../Renderers/CustomTraceRenderer";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../Renderers/useNavigationHandlers", () => ({
  useNavigationHandlers: () => ({ handleTraceClick: vi.fn(), handleSpanClick: vi.fn() }),
}));

const coverage = (request_count) => ({
  scope: "observed_requests", source_summary_present: true,
  request_count, coverage_unknown: true,
});
const body = (rows, companion) => ({
  status: true,
  result: { metadata: { total_rows: rows.length }, table: rows, config: [],
    ...(companion ? { trace_capture_coverage: companion } : {}),
  },
});

const traceRow = (count, cost, total_tokens) => {
  const raw = { project_id: "project", trace_id: "same", cost, total_tokens,
    "eval-config": { pass: 2 }, "arbitrary.attribute": [1, "value"] };
  return parseTraceObserveListResponse(body([raw], { project: { same: coverage(count) } }))
    .table.map(boundObserveListRow)[0];
};

describe("typed capture companion reaches actual list cells", () => {
  it("keeps project identities, dynamic eval cells and numeric values intact", () => {
    const parsed = parseTraceObserveListResponse(body([
      { project_id: "p", trace_id: "same", cost: 0, "eval-config": { fail: 3 } },
      { project_id: "q", trace_id: "same", cost: 1.25 },
    ], { p: { same: coverage(0) }, q: { same: coverage(3) } }));
    expect(parsed.table.map((r) => r._captureCoverage.request_count)).toEqual([0, 3]);
    expect(parsed.table.map((r) => r.cost)).toEqual([0, 1.25]);
    expect(parsed.table[0]["eval-config"]).toEqual({ fail: 3 });
    expect(traceRow(3, null, 43071)["arbitrary.attribute"]).toEqual([1, "value"]);
  });

  it("rejects invalid generated sidecars instead of accepting arbitrary row JSON", () => {
    expect(() => parseTraceObserveListResponse(body([], {
      p: { same: { ...coverage(0), request_count: -1 } },
    }))).toThrow();
    const row = parseTraceObserveListResponse(body([
      { cost: 0, capture_coverage: coverage(0) },
    ])).table[0];
    const { container } = render(<CostCell value={0} data={row} />);
    expect(container.textContent).toBe("$0.00");
  });

  it.each([
    [0, 0, 0, "Observed-only · $0.00", "Observed-only · 0 tokens"],
    [3, null, 43071, "Observed-only · Unknown", "Observed-only · 43,071 tokens"],
    [3, 0, 43071, "Observed-only · $0.00", "Observed-only · 43,071 tokens"],
  ])("renders source-only, late children and replay without a completeness claim (%s)",
    (count, cost, tokens, costLabel, tokenLabel) => {
      const row = traceRow(count, cost, tokens);
      const { rerender, container } = render(<CostCell value={cost} data={row} />);
      expect(container.textContent).toBe(costLabel);
      rerender(<TokenCell value={tokens} data={row} />);
      expect(container.textContent).toBe(tokenLabel);
      expect(row.cost).toBe(cost);
      expect(row.total_tokens).toBe(tokens);
      rerender(<TokenCell value={tokens} data={traceRow(count, cost, tokens)} />);
      expect(container.textContent).toBe(tokenLabel);
    });

  it.each([
    [0, 0, "Observed-only · $0.00"],
    [3, null, "Observed-only · Unknown"],
    [3, 1.25, "Observed-only · $1.25"],
  ])("dispatches qualified cost through the actual grid selector (%s, %s)",
    async (count, cost, label) => {
      const colDef = getTraceListColumnDefs({ id: "cost", name: "Total Cost", isVisible: true });
      const params = { colDef, value: cost, data: traceRow(count, cost, 43071) };
      const selected = colDef.cellRendererSelector(params);
      expect(selected?.component).toBe(CustomTraceRenderer);
      const Renderer = selected.component;
      const { container } = render(<Renderer {...params} />);
      expect(container.textContent).toBe(label);
      fireEvent.mouseOver(screen.getByText(label));
      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        `Capture coverage unknown. ${count} observed requests. Source summaries are nonadditive; observed amounts do not prove complete capture.`,
      );
      expect(params.data.cost).toBe(cost);
    });

  it("keeps legacy null prices and unrelated empty cells on the original grid path", () => {
    for (const id of ["cost", "input", "output"]) {
      const colDef = getTraceListColumnDefs({ id, name: id, isVisible: true });
      const data = id === "cost" ? { cost: null } : traceRow(3, null, 43071);
      const params = { colDef, value: null, data };
      expect(colDef.cellRendererSelector(params)).toBeNull();
      expect(colDef.valueFormatter(params)).toBe("-");
    }
  });

  it("explains the literal observed count, not an inferred complete turn", async () => {
    render(<CostCell value={0} data={traceRow(0, 0, 0)} />);
    fireEvent.mouseOver(screen.getByText("Observed-only · $0.00"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Capture coverage unknown. 0 observed requests. Source summaries are nonadditive; observed amounts do not prove complete capture.",
    );
  });

  it.each([[0, 0, 0], [3, null, 43071], [3, 0, 43071]])(
    "retains the declared session row companion (%s)", (count, cost, tokens) => {
      const row = parseSessionObserveListResponse(body([{
        session_id: "s", total_cost: cost, total_tokens: tokens,
        capture_coverage: coverage(count),
      }])).table[0];
      const { container, rerender } = render(<SessionCellRenderer column={{ colId: "total_cost" }} value={cost} data={row} />);
      expect(container.textContent).toBe(`Observed-only · ${cost === null ? "Unknown" : "$0.00"}`);
      rerender(<SessionCellRenderer column={{ colId: "total_tokens" }} value={tokens} data={row} />);
      expect(container.textContent).toBe(`Observed-only · ${tokens === 0 ? "0" : "43,071"} tokens`);
      expect(row.total_cost).toBe(cost);
    },
  );

  it("keeps the full observed token value and uncertainty in its tooltip", async () => {
    render(
      <SessionCellRenderer
        column={{ colId: "total_tokens" }}
        value={43071}
        data={{ capture_coverage: coverage(3) }}
      />,
    );
    const label = screen.getByText("Observed-only · 43,071 tokens");
    expect(label).toHaveClass("MuiTypography-noWrap");
    fireEvent.mouseOver(label);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Observed-only · 43,071 tokens. Capture coverage unknown. 3 observed requests. Source summaries are nonadditive; observed amounts do not prove complete capture.",
    );
  });

  it("leaves ordinary legacy cost, token and session rendering alone", () => {
    const { container, rerender } = render(<TokenCell value={0} data={{}} />);
    expect(container.textContent).toBe("-");
    rerender(<CostCell value={null} data={{}} />);
    expect(container.textContent).toBe("-");
    rerender(<SessionCellRenderer column={{ colId: "total_cost" }} value={0} data={{}} />);
    expect(container.textContent).toBe("$0.00");
  });
});
