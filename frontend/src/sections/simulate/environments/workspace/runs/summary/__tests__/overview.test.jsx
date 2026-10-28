import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

// Both run sources are mocked: the table's page and the chart's every-run
// list. The runs carry inline scores (as a mock run does), so no kpis fetch.
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEnvironmentRuns: vi.fn() };
});
vi.mock("src/api/simulate-environments/useAllRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useAllRuns: vi.fn() };
});

const { useEnvironmentRuns } = await import(
  "src/api/simulate-environments/runs"
);
const { useAllRuns } = await import("src/api/simulate-environments/useAllRuns");
const { default: RunsSummary } = await import("../RunsSummary");

const SCORES_1 = { e1: 90, e2: 40, e3: 70, e4: 55, e5: 85, e6: 60 };
const SCORES_2 = { e1: 92, e2: 49, e3: 61, e4: 55, e5: 80, e6: 75 };

const run = (n, over = {}) => ({
  id: `ex${n}`,
  executionId: `ex${n}`,
  ordinal: n,
  label: `Run ${n}`,
  status: "passed",
  runState: "finished",
  startedAt: "2026-01-12T11:05:00.000Z",
  finishedAt: "2026-01-12T11:07:30.000Z",
  total: 20,
  passed: 10,
  failed: 10,
  agentVersion: "v1",
  durationS: 12,
  scores: {},
  ...over,
});

// Newest first, as the API sends them. Run 4 is still running, so it is not
// on the chart; Run 3 is the latest finished run, on a new agent version.
const RUNS = [
  run(4, {
    status: "running",
    runState: "running",
    finishedAt: null,
    passed: 2,
    failed: 0,
  }),
  run(3, { agentVersion: "v2", passed: 15, failed: 5, scores: SCORES_2 }),
  run(2, { passed: 10, failed: 10, scores: SCORES_1 }),
  run(1, { passed: 8, failed: 12, scores: { e1: 80 } }),
];

const env = { id: "env-1", name: "Refund Support" };
const envState = { scenarios: [] };

function renderSummary({
  runs = RUNS,
  capped = false,
  onOpenRun = vi.fn(),
  allRuns = { runs, capped, isLoading: false },
} = {}) {
  useEnvironmentRuns.mockReturnValue({
    runs,
    count: runs.length,
    isLoading: false,
  });
  useAllRuns.mockReturnValue(allRuns);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RunsSummary
          env={env}
          envState={envState}
          onOpenRun={onOpenRun}
          onGo={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onOpenRun };
}

const chart = () =>
  screen.getByRole("group", { name: "Pass rate across runs" });
const dashedLines = () =>
  chart().querySelectorAll("polyline[stroke-dasharray]");

describe("Runs overview", () => {
  beforeEach(() => {
    useEnvironmentRuns.mockReset();
    useAllRuns.mockReset();
  });

  it('headline reads "NN% pass rate · Run N ▲x pts vs Run M" from real passed/total', () => {
    renderSummary();
    const headline = screen.getByTestId("runs-headline");
    // Run 3: 15/20 = 75%; Run 2: 10/20 = 50%.
    expect(headline.textContent.replace(/\s+/g, " ").trim()).toMatch(
      /^75% ?pass rate · Run 3 ?▲ ?25 pts vs Run 2$/,
    );
    expect(headline.textContent).not.toMatch(/scenario pass rate/);
  });

  it("headline drops the arrow when the latest run fell", () => {
    renderSummary({
      runs: [
        run(2, { passed: 6, failed: 14 }),
        run(1, { passed: 10, failed: 10 }),
      ],
    });
    expect(
      screen.getByTestId("runs-headline").textContent.replace(/\s+/g, " "),
    ).toMatch(/30% ?pass rate · Run 2 ?▼ ?20 pts vs Run 1/);
  });

  it("headline names the chart when no run has finished", () => {
    renderSummary({ runs: [RUNS[0]] });
    expect(screen.getByText("Pass rate by run")).toBeInTheDocument();
    expect(
      screen.getByText(
        "No finished run yet. The line starts with the first one.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the chart loading, not an empty history, while every run loads", () => {
    renderSummary({ allRuns: { runs: [], capped: false, isLoading: true } });
    expect(screen.queryByText(/No finished run yet/)).toBeNull();
    expect(
      screen.getByRole("progressbar", { name: "Loading runs" }),
    ).toBeInTheDocument();
  });

  it("noise is a soon chip in the tooltip", async () => {
    renderSummary();
    fireEvent.mouseOver(screen.getByText(/25 pts vs Run 2/));
    const tip = await screen.findByRole("tooltip");
    expect(within(tip).getByText(/15 of 20 calls passed/)).toBeInTheDocument();
    expect(within(tip).getByText("Noise soon")).toBeInTheDocument();
  });

  it("chart plots every finished run, marks agent-version changes, and a click opens the run", () => {
    const { onOpenRun } = renderSummary();
    // One point per finished run (the running Run 4 is left off).
    expect(chart().querySelectorAll("circle")).toHaveLength(3);
    // One dashed rule where the agent went from v1 to v2, each version labelled.
    expect(chart().querySelectorAll("line[stroke-dasharray]")).toHaveLength(1);
    expect(within(chart()).getByText("v1")).toBeInTheDocument();
    expect(within(chart()).getByText("v2")).toBeInTheDocument();
    // The points sit left to right, oldest to latest, higher for a better run.
    const circles = [...chart().querySelectorAll("circle")];
    const xs = circles.map((c) => Number(c.getAttribute("cx")));
    const ys = circles.map((c) => Number(c.getAttribute("cy")));
    expect(xs[0]).toBeLessThan(xs[1]);
    expect(xs[1]).toBeLessThan(xs[2]);
    expect(ys[2]).toBeLessThan(ys[1]); // 75% above 50%
    expect(ys[1]).toBeLessThan(ys[0]); // 50% above 40%

    fireEvent.click(within(chart()).getByRole("link", { name: "Open Run 3" }));
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun.mock.calls[0][0]).toMatchObject({ executionId: "ex3" });
  });

  it("chips show the four lowest evals with change vs the previous run and overlay on click; More evals lists the rest", async () => {
    renderSummary();
    const chips = screen.getAllByRole("button", { pressed: false });
    const names = chips.map((c) => c.textContent);
    // Lowest four on Run 3: E2 49, E4 55, E3 61, E6 75.
    expect(names).toEqual(["E249%+9", "E455%", "E361%−9", "E675%+15"]);

    expect(dashedLines()).toHaveLength(0);
    fireEvent.click(chips[0]);
    expect(
      screen.getByRole("button", { name: /E2/, pressed: true }),
    ).toBeInTheDocument();
    // E2's own dashed line over the pass rate, one point per run that scored it.
    expect(dashedLines()).toHaveLength(1);
    expect(dashedLines()[0].getAttribute("points").split(" ")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: /More evals/ }));
    const menu = await screen.findByRole("menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["E580%", "E192%"]);
    fireEvent.click(items[1]);
    expect(dashedLines()).toHaveLength(1);
    // E1 scored on all three runs.
    expect(dashedLines()[0].getAttribute("points").split(" ")).toHaveLength(3);
    expect(screen.queryByRole("button", { pressed: true })).toBeNull();
  });

  it("offers the table's evals even when the latest runs were not scored", () => {
    renderSummary({
      runs: [
        run(3, { passed: 15, failed: 5 }),
        run(2, { passed: 10, failed: 10 }),
        run(1, { passed: 8, failed: 12, scores: { e1: 80 } }),
      ],
    });
    const chips = screen.getAllByRole("button", { pressed: false });
    expect(chips.map((c) => c.textContent)).toEqual(["E1-"]);
    fireEvent.click(chips[0]);
    // Its line has the one point where a run scored it.
    expect(dashedLines()[0].getAttribute("points").split(" ")).toHaveLength(1);
  });

  it('over 200 runs says "Showing the latest 200 runs"', () => {
    renderSummary({ capped: true });
    expect(screen.getByText(/Showing the latest 200 runs/)).toBeInTheDocument();
  });

  it("says nothing about a cap when every run is on the chart", () => {
    renderSummary();
    expect(screen.queryByText(/Showing the latest/)).toBeNull();
  });
});
