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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const cancelMutate = vi.fn((id, opts) => opts?.onSuccess?.());
vi.mock("src/sections/common/simulation/hooks/useCancelExecution", () => ({
  useCancelExecution: () => ({ mutate: cancelMutate, isPending: false }),
}));
const navigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useNavigate: () => navigate };
});
// The real drawer reads the environment and the eval catalogue on open.
vi.mock("../../../evals/AddEvaluationDrawer", () => ({
  default: ({ open, env }) =>
    open ? (
      <div role="dialog" aria-label="Add evaluations">
        {env.id}
      </div>
    ) : null,
}));

const { useEnvironmentRuns } = await import(
  "src/api/simulate-environments/runs"
);
const { useAllRuns } = await import("src/api/simulate-environments/useAllRuns");
const { enqueueSnackbar } = await import("notistack");
const { default: axios } = await import("src/utils/axios");
const { paths } = await import("src/routes/paths");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../../../store/useEnvironmentsStore"
);
const { UI_ONLY_SAVED } = await import("../../../gaps/NeedsInputButton");
const { default: RunsSummary } = await import("../RunsSummary");

const run = (n, over = {}) => ({
  id: `ex${n}`,
  executionId: `ex${n}`,
  ordinal: n,
  label: `Run ${n}`,
  status: "passed",
  runState: "finished",
  stoppable: false,
  startedAt: "2026-01-12T11:05:00.000Z",
  finishedAt: "2026-01-12T11:07:30.000Z",
  total: 20,
  passed: 10,
  failed: 10,
  agentVersion: "v1",
  durationS: 30,
  scenarioCount: 5,
  trials: 4,
  scores: {},
  ...over,
});

// Newest first, as the API sends them. Run 4 is still going.
const RUNS = [
  run(4, {
    status: "running",
    runState: "running",
    stoppable: true,
    finishedAt: null,
    passed: 2,
    failed: 0,
  }),
  run(3, {
    agentVersion: "v2",
    passed: 15,
    failed: 5,
    durationS: 60,
    scores: { e1: 80, e2: 40 },
  }),
  run(2, { passed: 10, failed: 10, durationS: 10, scores: { e1: 70, e2: 50 } }),
  run(1, { passed: 8, failed: 12, durationS: 30, scores: { e1: 60, e2: 45 } }),
];

const env = { id: "env-1", name: "Refund Support" };
const envState = { scenarios: [] };

function renderSummary({ runs = RUNS } = {}) {
  useEnvironmentRuns.mockReturnValue({
    runs,
    count: runs.length,
    isLoading: false,
  });
  useAllRuns.mockReturnValue({ runs, capped: false, isLoading: false });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onOpenRun = vi.fn();
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

const table = () => screen.getByRole("table");
const rowOf = (n) =>
  within(table())
    .getByText(new RegExp(`^Run ${n} · agent`))
    .closest("tr");
const tick = (n) =>
  fireEvent.click(screen.getByRole("checkbox", { name: `Select Run ${n}` }));
const chart = () =>
  screen.getByRole("group", { name: "Pass rate across runs" });

const writes = ["post", "put", "patch", "delete"];
function expectNothingSent() {
  writes.forEach((m) => expect(axios[m]).not.toHaveBeenCalled());
  expect(cancelMutate).not.toHaveBeenCalled();
}

describe("Runs table", () => {
  beforeEach(() => {
    useEnvironmentRuns.mockReset();
    useAllRuns.mockReset();
    enqueueSnackbar.mockClear();
    cancelMutate.mockClear();
    navigate.mockClear();
    resetEnvironmentsStore();
    writes.forEach((m) => vi.spyOn(axios, m).mockResolvedValue({ data: {} }));
  });

  it('columns follow Vel; Calls tooltip reads "N scenarios × K repeats"; Flaky, Started by, Said not done, Return, Tokens, Cost headers carry soon chips', async () => {
    renderSummary();
    const heads = within(table())
      .getAllByRole("columnheader")
      .map((th) => th.textContent.trim());
    expect(heads).toEqual([
      "",
      "Run",
      "Status",
      "Scenario pass soon",
      "Call pass",
      "Flaky soon",
      "Failing",
      "Calls",
      "Run time",
      "Started by soon",
      "Duration soon",
      "Tokens soon",
      "Cost soon",
      "Said, not done soon",
      "Return soon",
      "E1",
      "E2",
    ]);
    // The old Trials and Simulations columns are folded into Calls (R13).
    expect(screen.queryByRole("columnheader", { name: "Trials" })).toBeNull();
    expect(
      screen.queryByRole("columnheader", { name: "Simulations" }),
    ).toBeNull();

    const row = within(rowOf(3));
    expect(row.getByText("75%")).toBeInTheDocument();
    expect(row.getByText("5")).toBeInTheDocument(); // failed calls
    fireEvent.mouseOver(row.getByText("20"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("5 scenarios × 4 repeats");
  });

  it("Stop still works on running rows", () => {
    const { onOpenRun } = renderSummary();
    const stop = within(rowOf(4)).getByRole("button", {
      name: "Stop simulation",
    });
    expect(
      within(rowOf(3)).queryByRole("button", { name: "Stop simulation" }),
    ).toBeNull();
    fireEvent.click(stop);
    fireEvent.click(screen.getByRole("button", { name: "Stop run" }));
    expect(cancelMutate).toHaveBeenCalledTimes(1);
    expect(cancelMutate.mock.calls[0][0]).toBe("ex4");
    expect(onOpenRun).not.toHaveBeenCalled();
    // The confirm dialog is still closing, so read the box by its label.
    expect(screen.getByLabelText("Select Run 4")).not.toBeChecked();
  });

  it("ticking 2 finished runs enables Compare, which opens the compare page with ?runs=", () => {
    const { onOpenRun } = renderSummary();
    expect(screen.queryByRole("button", { name: "Compare" })).toBeNull();
    tick(3);
    tick(2);
    expect(onOpenRun).not.toHaveBeenCalled();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    const compare = screen.getByRole("button", { name: "Compare" });
    expect(compare).toBeEnabled();
    fireEvent.click(compare);
    // Oldest first, so the earlier run is the one the change is read from.
    expect(navigate).toHaveBeenCalledWith(
      `${paths.dashboard.simulate.environments.compare("env-1")}?runs=ex2,ex3`,
    );

    // A run still going has no results to compare.
    tick(4);
    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
  });

  it("the headline compares the latest run with the chosen baseline", () => {
    renderSummary();
    expect(screen.getByText(/pts vs Run 2/)).toBeInTheDocument();
    tick(1);
    fireEvent.click(screen.getByRole("button", { name: "Set as baseline" }));
    expect(screen.getByText(/pts vs Run 1/)).toBeInTheDocument();
    expect(screen.queryByText(/pts vs Run 2/)).toBeNull();
  });

  it("Set as baseline is flagged, sends nothing, is kept per environment in the workspace store, and turns on ± deltas computed from the real run numbers (noise is a soon chip)", () => {
    renderSummary();
    // No baseline, no deltas.
    expect(within(rowOf(3)).queryByText("25 pts")).toBeNull();

    tick(2);
    const set = screen.getByRole("button", { name: "Set as baseline" });
    expect(
      within(set.parentElement).getByLabelText("UI only"),
    ).toBeInTheDocument();
    fireEvent.click(set);

    expectNothingSent();
    expect(useEnvironmentsStore.getState().byEnv["env-1"].baselineRunId).toBe(
      "ex2",
    );
    expect(screen.queryByText("1 selected")).toBeNull();
    expect(screen.getByText(/Measured against/)).toHaveTextContent(
      "Measured against Run 2 · agent v1",
    );
    expect(screen.getByText("Noise soon")).toBeInTheDocument();
    expect(within(rowOf(2)).getByText("Baseline")).toBeInTheDocument();

    // Run 3 against Run 2: 75% vs 50% is 25 pts; 5 failed vs 10; 60s vs
    // 10s is 500% slower; E1 80 vs 70 and E2 40 vs 50 are 10 pts each way.
    const r3 = within(rowOf(3));
    expect(r3.getByText("25 pts")).toBeInTheDocument();
    expect(r3.getByText("500%")).toBeInTheDocument();
    expect(r3.getAllByText("10 pts")).toHaveLength(2);
    expect(r3.getByLabelText("5 fewer")).toBeInTheDocument();
    // Run 1: 40% vs 50% and E1 60 vs 70 are 10 pts; E2 45 vs 50 is 5.
    expect(within(rowOf(1)).getAllByText("10 pts")).toHaveLength(2);
    expect(within(rowOf(1)).getByText("5 pts")).toBeInTheDocument();
    // The baseline itself carries no change.
    expect(within(rowOf(2)).queryByText(/pts$/)).toBeNull();

    // Kept per environment: another environment has none.
    expect(useEnvironmentsStore.getState().byEnv["env-2"]).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: "Remove baseline" }));
    expect(
      useEnvironmentsStore.getState().byEnv["env-1"].baselineRunId,
    ).toBeNull();
    expect(within(rowOf(3)).queryByText("25 pts")).toBeNull();
  });

  it("Delete is flagged and sends nothing", () => {
    renderSummary();
    tick(2);
    const del = screen.getByRole("button", { name: "Delete" });
    expect(
      within(del.parentElement).getByLabelText("UI only"),
    ).toBeInTheDocument();
    fireEvent.click(del);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete 1 run?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expectNothingSent();
    // The run is still listed.
    expect(rowOf(2)).toBeInTheDocument();
  });

  it("agent-version chips filter the chart and table; environment version select is a soon chip", () => {
    renderSummary();
    const scope = screen.getByRole("radiogroup", { name: "Agent version" });
    const chips = within(scope).getAllByRole("radio");
    expect(chips.map((c) => c.getAttribute("aria-label"))).toEqual([
      "All, 4 runs",
      "v2, 1 run",
      "v1, 3 runs",
    ]);
    expect(chips[0]).toHaveAttribute("aria-checked", "true");
    expect(chart().querySelectorAll("circle")).toHaveLength(3);
    expect(screen.getByText("Environment version soon")).toBeInTheDocument();

    fireEvent.click(chips[2]);
    expect(chips[2]).toHaveAttribute("aria-checked", "true");
    expect(within(table()).queryByText(/^Run 3 · agent/)).toBeNull();
    expect(rowOf(4)).toBeInTheDocument();
    expect(rowOf(2)).toBeInTheDocument();
    expect(rowOf(1)).toBeInTheDocument();
    // Runs 2 and 1 are v1's finished runs.
    expect(chart().querySelectorAll("circle")).toHaveLength(2);
    expect(screen.getByText("3 of 4 runs")).toBeInTheDocument();
    expect(screen.getByText("Runs (3)")).toBeInTheDocument();
  });

  it("Choose winner ranks runs on real pass rate, duration and eval scores; saving the winner is flagged", () => {
    renderSummary();
    fireEvent.click(screen.getByRole("button", { name: "Choose winner" }));
    const drawer = within(
      screen.getByRole("dialog", { name: "Winner settings" }),
    );
    expect(drawer.getByLabelText("UI only")).toBeInTheDocument();
    // Ranked on what the runs list sends; the rest are soon.
    for (const m of ["Pass rate", "E1", "E2", "Run time"]) {
      expect(drawer.getByText(m)).toBeInTheDocument();
    }
    expect(drawer.getByText("Tokens soon")).toBeInTheDocument();
    expect(
      drawer.getByText("3 runs ranked", { exact: false }),
    ).toBeInTheDocument();

    // Balanced: the pass rate leads, so Run 3 (75%) wins.
    expect(drawer.getByTestId("winner-name")).toHaveTextContent(
      "Run 3 · agent v2",
    );
    // Speed first: Run 2 ran in 10s.
    fireEvent.click(drawer.getByRole("button", { name: "Speed first" }));
    expect(drawer.getByTestId("winner-name")).toHaveTextContent(
      "Run 2 · agent v1",
    );

    fireEvent.click(drawer.getByRole("button", { name: "Choose winner" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expectNothingSent();
    expect(within(table()).queryByText("Winner")).toBeNull();
  });

  it("Add Evals opens the evals drawer in place", () => {
    renderSummary();
    expect(
      screen.queryByRole("dialog", { name: "Add evaluations" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add Evals" }));
    expect(
      screen.getByRole("dialog", { name: "Add evaluations" }),
    ).toHaveTextContent("env-1");
  });
});
