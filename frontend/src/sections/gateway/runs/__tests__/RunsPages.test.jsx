import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import RunsSection from "../RunsSection";
import RunDetailSection from "../RunDetailSection";
import { grid, mountedGrids } from "../../logs/__tests__/fakeAgGrid";
import {
  CELL_PADDING_PX,
  CELL_TEXT_PX,
  GRID_WIDTH_1440,
  SCROLLBAR_PX,
  columnOf,
  cutHeaders,
  flexWidths,
  gridBudget1440,
  minimumGridWidth,
} from "../../logs/__tests__/gridFit";

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));

const { mockGet, drawer } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  drawer: { props: null },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: { gateway: { requestLogs: "/agentcc/request-logs/" } },
}));
vi.mock("../../logs/RequestDetailDrawer", () => ({
  default: (props) => {
    drawer.props = props;
    return props.open ? <div role="dialog">drawer {props.logId}</div> : null;
  },
}));
// Run links as they are once the runs pages are visible.
vi.mock("../../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));

const row = (over) => ({
  request_id: `req-${over.id}`,
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 200,
  is_error: false,
  latency_ms: 10,
  cost: "0.000042",
  total_tokens: 14,
  agent_id: "support-agent",
  ...over,
});

const ROWS = [
  row({
    id: "a5",
    agent_run_id: "run-2",
    agent_step: "final",
    started_at: "2026-10-20T09:32:45.000Z",
  }),
  row({
    id: "a4",
    agent_run_id: "run-2",
    agent_step: "plan",
    started_at: "2026-10-20T09:32:44.000Z",
    status_code: 502,
    is_error: true,
  }),
  row({ id: "x1", agent_run_id: null, started_at: "2026-10-20T09:32:43.500Z" }),
  row({
    id: "a2",
    agent_run_id: "run-1",
    agent_step: "final",
    started_at: "2026-10-20T09:32:42.000Z",
    agent_parent_run_id: "root-run",
  }),
  row({
    id: "a1",
    agent_run_id: "run-1",
    agent_step: "plan",
    started_at: "2026-10-20T09:32:41.000Z",
    agent_parent_run_id: "root-run",
  }),
];

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname + location.search}</div>;
}

function renderAt(url) {
  return render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={[url]}>
            <Routes>
              <Route path="/dashboard/gateway/runs" element={<RunsSection />} />
              <Route
                path="/dashboard/gateway/runs/:runId"
                element={<RunDetailSection />}
              />
              <Route path="*" element={null} />
            </Routes>
            <Where />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe("Runs list", () => {
  it("groups the newest requests by run id and says which window it read", async () => {
    mockGet.mockResolvedValue({ data: { count: 1234, results: ROWS } });
    renderAt("/dashboard/gateway/runs");
    expect(
      screen.getByRole("heading", { level: 1, name: "Runs" }),
    ).toBeInTheDocument();
    await screen.findByRole("link", { name: "run-2" });
    const [url, { params }] = mockGet.mock.calls[0];
    expect(url).toBe("/agentcc/request-logs/");
    expect(params).toMatchObject({
      limit: 500,
      page: 1,
      ordering: "-started_at",
    });
    expect(
      screen.getByText(/Runs in the newest 5 of 1,234 requests/),
    ).toBeInTheDocument();
    const rows = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    expect(rows).toHaveLength(2);
    expect(
      within(rows[0]).getByRole("link", { name: "run-2" }),
    ).toHaveAttribute("href", "/dashboard/gateway/runs/run-2");
    expect(rows[0]).toHaveTextContent("support-agent");
    expect(rows[0]).toHaveTextContent("plan → final");
    expect(grid.props.getRowId({ data: { runId: "run-2" } })).toBe("run-2");
  });

  it("opens any run by its id, also one older than the window", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({ data: { count: 5, results: ROWS } });
    renderAt("/dashboard/gateway/runs");
    await screen.findByRole("link", { name: "run-2" });
    await user.type(
      screen.getByLabelText("Open a run by id"),
      "run/old 7{Enter}",
    );
    expect(screen.getByTestId("where")).toHaveTextContent(
      "/dashboard/gateway/runs/run%2Fold%207",
    );
  });

  it("explains how runs appear when there are none", async () => {
    mockGet.mockResolvedValue({ data: { count: 1, results: [ROWS[2]] } });
    renderAt("/dashboard/gateway/runs");
    expect(await screen.findByText("No agent runs yet")).toBeInTheDocument();
    expect(screen.getByText(/x-agentcc-run-id/)).toBeInTheDocument();
  });

  it("shows an error with Retry", async () => {
    const user = userEvent.setup();
    mockGet.mockRejectedValueOnce({ message: "Gateway API unreachable" });
    renderAt("/dashboard/gateway/runs");
    expect(
      await screen.findByText("Runs could not be loaded"),
    ).toBeInTheDocument();
    mockGet.mockResolvedValue({ data: { count: 5, results: ROWS } });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("link", { name: "run-2" }),
    ).toBeInTheDocument();
  });
});

describe("Run detail", () => {
  const RUN = ROWS.filter((r) => r.agent_run_id === "run-1");
  const calls = (count) =>
    Array.from({ length: count }, (_, i) =>
      row({
        id: `call-${i}`,
        agent_run_id: "run-1",
        agent_id: null,
        started_at: new Date(
          Date.UTC(2026, 9, 20, 9, 32, 41) + i * 1000,
        ).toISOString(),
        is_error: i === count - 1,
        status_code: i === count - 1 ? 502 : 200,
      }),
    );

  it("reads every request of the run through the run_id filter", async () => {
    mockGet.mockResolvedValue({ data: { count: 2, results: RUN } });
    renderAt("/dashboard/gateway/runs/run-1");
    expect(
      await screen.findByRole("heading", { level: 1, name: "run-1" }),
    ).toBeInTheDocument();
    const [url, { params }] = mockGet.mock.calls[0];
    expect(url).toBe("/agentcc/request-logs/");
    expect(params).toMatchObject({
      run_id: "run-1",
      limit: 500,
      ordering: "started_at",
    });
    expect(screen.getByRole("link", { name: /Runs/ })).toHaveAttribute(
      "href",
      "/dashboard/gateway/runs",
    );
    expect(screen.getByRole("link", { name: "Open in Logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs?run_id=run-1",
    );
    expect(
      await screen.findByRole("link", { name: "root-run" }),
    ).toHaveAttribute("href", "/dashboard/gateway/runs/root-run");
  });

  it("shows the run's calls on one axis and opens a call's request", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({ data: { count: 2, results: RUN } });
    renderAt("/dashboard/gateway/runs/run-1");
    const timeline = within(
      await screen.findByRole("list", { name: "Run timeline" }),
    );
    const calls = timeline.getAllByRole("listitem");
    expect(calls).toHaveLength(2);
    expect(calls[0]).toHaveTextContent("plan");
    expect(calls[1]).toHaveTextContent("+1.00 s");
    await user.click(within(calls[1]).getByRole("button"));
    expect(screen.getByRole("dialog")).toHaveTextContent("drawer a2");
  });

  it("breaks the run down by step and by model", async () => {
    mockGet.mockResolvedValue({ data: { count: 2, results: RUN } });
    renderAt("/dashboard/gateway/runs/run-1");
    expect(
      await screen.findByRole("heading", { name: "Steps" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Models" })).toBeInTheDocument();
    const summary = within(screen.getByRole("list", { name: "Summary" }));
    expect(summary.getByText("2")).toBeInTheDocument();
    expect(summary.getByText("support-agent")).toBeInTheDocument();
  });

  it("says so when no request carries the run id", async () => {
    mockGet.mockResolvedValue({ data: { count: 0, results: [] } });
    renderAt("/dashboard/gateway/runs/nope");
    expect(
      await screen.findByText("No requests carry this run id"),
    ).toBeInTheDocument();
  });

  it("puts the partial-run notice before the summary and labels loaded totals and observed time", async () => {
    mockGet.mockResolvedValue({ data: { count: 700, results: calls(500) } });
    renderAt("/dashboard/gateway/runs/run-1");
    const notice = await screen.findByText("Partial run: 500 of 700 calls");
    const summaryElement = screen.getByRole("list", { name: "Summary" });
    expect(
      notice.compareDocumentPosition(summaryElement) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Summary, timeline and breakdowns cover only the loaded calls. Open in Logs to investigate all calls.",
    );
    const summary = within(summaryElement);
    expect(summary.getByText("Loaded calls").closest("li")).toHaveTextContent(
      "500",
    );
    expect(
      summary.getByText("Errors in loaded calls").closest("li"),
    ).toHaveTextContent("1");
    expect(summary.getByText("Cost of loaded calls")).toBeInTheDocument();
    expect(
      summary.getByText("Observed duration").closest("li"),
    ).toHaveTextContent("499.01 s");
    expect(summary.getByText("Earliest loaded call")).toBeInTheDocument();
    ["Requests", "Errors", "Cost", "Duration", "Started"].forEach((label) => {
      expect(
        summary.queryByText(label, { exact: true }),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.getByText("500 loaded calls · Observed duration: 499.01 s"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Offsets are relative to the earliest loaded call, not the full run's start.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Loaded requests that carried this run id."),
    ).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockGet).toHaveBeenCalledWith("/agentcc/request-logs/", {
      params: { run_id: "run-1", page: 1, limit: 500, ordering: "started_at" },
    });
  });

  it.each([
    { count: 2, duration: "1.01 s" },
    { count: 500, duration: "499.01 s" },
  ])(
    "keeps normal labels when all $count calls are loaded",
    async ({ count, duration }) => {
      mockGet.mockResolvedValue({ data: { count, results: calls(count) } });
      renderAt("/dashboard/gateway/runs/run-1");
      const summary = within(
        await screen.findByRole("list", { name: "Summary" }),
      );
      ["Requests", "Errors", "Cost", "Duration", "Started"].forEach((label) => {
        expect(summary.getByText(label, { exact: true })).toBeInTheDocument();
      });
      expect(screen.queryByText(/Partial run:/)).not.toBeInTheDocument();
      expect(summary.queryByText("Observed duration")).not.toBeInTheDocument();
      expect(screen.queryByText(/Offsets are relative/)).not.toBeInTheDocument();
      expect(
        screen.getByText(`${count} calls over ${duration}`),
      ).toBeInTheDocument();
      expect(
        screen.getByText("Every request that carried this run id."),
      ).toBeInTheDocument();
      expect(mockGet).toHaveBeenCalledTimes(1);
    },
  );

  it("opens Logs with the encoded run id without another request for partial-run labeling", async () => {
    const user = userEvent.setup();
    const runId = "run/old 7&branch=2?#";
    mockGet.mockResolvedValue({
      data: {
        count: 700,
        results: RUN.map((call) => ({ ...call, agent_run_id: runId })),
      },
    });
    renderAt(`/dashboard/gateway/runs/${encodeURIComponent(runId)}`);
    expect(
      await screen.findByText("Partial run: 2 of 700 calls"),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open in Logs" });
    const href = "/dashboard/gateway/logs?run_id=run%2Fold+7%26branch%3D2%3F%23";
    expect(link).toHaveAttribute("href", href);
    expect(mockGet).toHaveBeenCalledWith("/agentcc/request-logs/", {
      params: { run_id: runId, page: 1, limit: 500, ordering: "started_at" },
    });
    await user.click(link);
    expect(screen.getByTestId("where")).toHaveTextContent(href);
    expect(mockGet).toHaveBeenCalledTimes(1);
  });
});

describe("Runs grid widths", () => {
  const renderRuns = async () => {
    mockGet.mockResolvedValue({ data: { count: 5, results: ROWS } });
    renderAt("/dashboard/gateway/runs");
    await screen.findByRole("link", { name: "run-2" });
    return grid.props;
  };

  it("cuts off no header at its minimum width, the hidden ones included", async () => {
    expect(cutHeaders(await renderRuns())).toEqual([]);
  });

  it("fits at 1440 px without scrolling sideways, and Steps takes the spare room", async () => {
    const props = await renderRuns();
    expect(minimumGridWidth(props)).toBeLessThanOrEqual(gridBudget1440());
    const widths = flexWidths(props, GRID_WIDTH_1440.page - SCROLLBAR_PX);
    expect(widths.steps).toBeGreaterThanOrEqual(
      CELL_TEXT_PX["demo steps, widest"] + CELL_PADDING_PX,
    );
  });

  it("keeps Tokens and Models one choice away: hidden by default, in the grid's column chooser", async () => {
    const props = await renderRuns();
    expect(columnOf(props, "tokens")).toMatchObject({ hide: true });
    expect(columnOf(props, "models")).toMatchObject({ hide: true });
    expect(screen.getByTestId("ag-grid")).not.toHaveTextContent("Models");
  });

  it("shows run ids in the grid's own type like the other cells, not monospace", async () => {
    await renderRuns();
    const link = within(screen.getByTestId("ag-grid")).getByRole("link", {
      name: "run-2",
    });
    expect(link).toHaveClass("MuiTypography-inherit");
    expect(getComputedStyle(link).fontFamily).not.toMatch(/monospace/);
  });

  it("cuts off no header in the run's step and model grids", async () => {
    mockGet.mockResolvedValue({
      data: {
        count: 2,
        results: ROWS.filter((r) => r.agent_run_id === "run-1"),
      },
    });
    renderAt("/dashboard/gateway/runs/run-1");
    await screen.findByRole("heading", { name: "Steps" });
    const grids = mountedGrids();
    expect(grids).toHaveLength(2);
    grids.forEach((props) => expect(cutHeaders(props)).toEqual([]));
  });
});

// A run whose calls had no price never reads
// as free. totals() counts the unpriced calls; the run's Cost, the Runs
// grid's Cost, the Step and Model breakdowns read a floor or an em dash as
// Logs and Analytics do, and an unpriced call's line reads "No price found".
describe("Runs and run detail over unpriced calls", () => {
  const unpriced = (over) =>
    row({ ...over, cost: "0", cost_source: "unpriced", model: "o1-mini" });
  const MIXED = [
    row({
      id: "m1",
      agent_run_id: "run-m",
      agent_step: "plan",
      started_at: "2026-10-20T09:32:41.000Z",
      cost: "0.0042",
    }),
    unpriced({
      id: "m2",
      agent_run_id: "run-m",
      agent_step: "final",
      started_at: "2026-10-20T09:32:42.000Z",
    }),
  ];
  const ALL_UNPRICED = [
    unpriced({
      id: "u1",
      agent_run_id: "run-u",
      agent_step: "plan",
      started_at: "2026-10-20T09:32:43.000Z",
    }),
    unpriced({
      id: "u2",
      agent_run_id: "run-u",
      agent_step: "final",
      started_at: "2026-10-20T09:32:44.000Z",
    }),
  ];

  const costCell = (rowEl) => {
    const headers = within(screen.getByTestId("ag-grid"))
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    return within(rowEl).getAllByRole("cell")[headers.indexOf("Cost")];
  };

  it("reads a floor for a run with some unpriced calls and an em dash for one with none priced", async () => {
    mockGet.mockResolvedValue({
      data: { count: 4, results: [...ALL_UNPRICED, ...MIXED] },
    });
    renderAt("/dashboard/gateway/runs");
    await screen.findByRole("link", { name: "run-u" });
    const rows = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    const byRun = (id) =>
      rows.find((r) => within(r).queryByRole("link", { name: id }));
    const mixed = costCell(byRun("run-m"));
    expect(mixed).toHaveTextContent(/^≥\s\$0\.0042$/);
    expect(mixed.querySelector("[title]")).toHaveAttribute(
      "title",
      expect.stringContaining("1 of 2 requests had no price found"),
    );
    const none = costCell(byRun("run-u"));
    expect(none).toHaveTextContent("—");
    expect(none).not.toHaveTextContent("$0.00");
    // An unknown cost sorts as missing, not as 0.
    const cost = grid.props.columnDefs.find((c) => c.colId === "cost");
    expect(
      cost.valueGetter({
        data: { cost: 0, unpriced: 2, requests: 2 },
      }),
    ).toBeNull();
  });

  it.each([2, 700])(
    "reads the run's Cost, breakdowns and each unpriced call honestly with total count %i",
    async (count) => {
      mockGet.mockResolvedValue({ data: { count, results: MIXED } });
      renderAt("/dashboard/gateway/runs/run-m");
      const summary = within(
        await screen.findByRole("list", { name: "Summary" }),
      );
      expect(
        summary.getByText(count > 2 ? "Cost of loaded calls" : "Cost"),
      ).toBeInTheDocument();
      const cost = summary.getByText(/^≥\s\$0\.0042$/);
      expect(cost.closest("[title]")).toHaveAttribute(
        "title",
        expect.stringContaining("1 of 2 requests had no price found"),
      );
      const timeline = within(screen.getByRole("list", { name: "Run timeline" }));
      const calls = timeline.getAllByRole("listitem");
      expect(calls[1]).not.toHaveTextContent("$0.00");
      expect(within(calls[1]).getByTitle("No price found")).toHaveTextContent(
        "—",
      );
      // The Step and Model breakdowns: the unpriced step reads an em dash.
      const grids = mountedGrids().filter((props) =>
        props.columnDefs.some((c) => c.headerName === "Cost"),
      );
      const stepGrid = grids.find((props) =>
        props.columnDefs.some((c) => c.headerName === "Step"),
      );
      const costCol = stepGrid.columnDefs.find((c) => c.headerName === "Cost");
      const finalStep = stepGrid.rowData.find((r) => r.key === "final");
      expect(
        costCol.valueFormatter({ data: finalStep, value: finalStep.cost }),
      ).toBe("—");
      expect(costCol.valueGetter({ data: finalStep })).toBeNull();
    },
  );

  it.each([2, 700])(
    "reads an em dash for a run whose calls all had no price with total count %i",
    async (count) => {
      mockGet.mockResolvedValue({ data: { count, results: ALL_UNPRICED } });
      renderAt("/dashboard/gateway/runs/run-u");
      const summary = within(
        await screen.findByRole("list", { name: "Summary" }),
      );
      expect(
        summary.getByText(count > 2 ? "Cost of loaded calls" : "Cost"),
      ).toBeInTheDocument();
      expect(summary.getByTitle("No price found")).toHaveTextContent("—");
      expect(summary.queryByText("$0.00")).toBeNull();
    },
  );
});
