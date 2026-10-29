import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/_fixtures/fixRuns";

const hooks = vi.hoisted(() => ({ fixRun: null, stop: null, options: null }));
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFixRun: (id) => (hooks.fixRun ? hooks.fixRun : actual.useFixRun(id)),
    useStopFixRun: (id) => (hooks.stop ? hooks.stop : actual.useStopFixRun(id)),
    useFixLaunchOptions: (id, o) =>
      hooks.options ? hooks.options : actual.useFixLaunchOptions(id, o),
  };
});

const { stepRows } = await import("../fixRunView");
const { default: TrialChart } = await import("../TrialChart");
const { default: TrialsTable } = await import("../TrialsTable");
const { default: FixVerdict } = await import("../FixVerdict");
const { default: FixDecisionDrawer } = await import("../FixDecisionDrawer");
const { default: FixRunPage } = await import("../FixRunPage");
const { default: LaunchFixDialog } = await import("../LaunchFixDialog");
const fixRuns = await import("src/api/simulate-environments/fixRuns");

const byId = (id) => ({
  ...structuredClone(FIX_RUN_FIXTURES.find((f) => f.id === id)),
  environment_id: "env-1",
});

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SnackbarProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );

const withPendingRate = () => {
  const fix = byId("fix-search");
  fix.candidates[1].verdict.target_pass_rate = null;
  return fix;
};

beforeEach(() => {
  hooks.fixRun = null;
  hooks.stop = null;
  hooks.options = null;
});

describe("a trial whose target rate has not arrived", () => {
  it("leaves it out of best-so-far", () => {
    const rows = stepRows(withPendingRate());
    expect(rows[1].lines[0]).toBe("2 of up to 4 trials · best so far 0% on 6 target scenarios");
  });

  it("leaves it off the chart", () => {
    const fix = withPendingRate();
    wrap(<TrialChart candidates={fix.candidates} />);
    expect(screen.getAllByTestId("trial-point")).toHaveLength(1);
  });

  it("draws no chart when no trial has a rate", () => {
    const fix = withPendingRate();
    fix.candidates[0].verdict.target_pass_rate = null;
    const { container } = wrap(<TrialChart candidates={fix.candidates} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("shows Pending in its Pass cell", () => {
    wrap(<TrialsTable fix={withPendingRate()} />);
    expect(screen.getByTestId("trial-2")).toHaveTextContent("Pending");
  });
});

describe("a verdict without tiers", () => {
  const noTiers = () => {
    const fix = byId("fix-ready-archive");
    fix.candidates.forEach((c) => delete c.verdict.tiers);
    return fix;
  };

  it("renders the verdict without a Checks row", () => {
    wrap(<FixVerdict fix={noTiers()} />);
    expect(screen.getByText(/failing scenarios on the held-back set/)).toBeInTheDocument();
    expect(screen.queryByText(/^Checks ·/)).toBeNull();
  });

  it("renders the decision drawer", () => {
    wrap(<FixDecisionDrawer fix={noTiers()} open onClose={vi.fn()} />);
    expect(screen.getByText("How it was decided")).toBeInTheDocument();
  });
});

describe("a failed poll", () => {
  it("keeps showing the fix it already has", () => {
    hooks.fixRun = { fix: byId("fix-search"), isLoading: false, error: new Error("network") };
    wrap(<FixRunPage envId="env-1" fixId="fix-search" />);
    expect(screen.getByText("Improvement 13")).toBeInTheDocument();
    expect(screen.queryByText("This improvement could not be found")).toBeNull();
  });
});

describe("launch options that fail to load", () => {
  it("say why and can be closed", async () => {
    const onClose = vi.fn();
    hooks.options = {
      options: undefined,
      isLoading: false,
      error: { statusCode: 402, upgrade_required: true, error: { message: "Fix my agent is not on your plan" } },
    };
    wrap(<LaunchFixDialog open envId="env-1" sourceRunId="te-run-6" onClose={onClose} />);
    expect(screen.getByText("Fix my agent is not on your plan")).toBeInTheDocument();
    expect(screen.queryByText("Loading the run…")).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("a Stop the server refuses", () => {
  it("shows the server's message", async () => {
    hooks.stop = {
      isPending: false,
      mutate: (_v, { onError } = {}) =>
        onError?.({ statusCode: 409, error: "fix_run_active", message: "The fix is already stopping" }),
    };
    hooks.fixRun = { fix: byId("fix-search"), isLoading: false, error: null };
    const user = userEvent.setup();
    wrap(<FixRunPage envId="env-1" fixId="fix-search" />);
    await user.click(screen.getByRole("button", { name: "Stop" }));
    const stops = screen.getAllByRole("button", { name: "Stop" });
    await user.click(stops[stops.length - 1]);
    expect(await screen.findByText("The fix is already stopping")).toBeInTheDocument();
  });
});

describe("Per scenario data", () => {
  it("treats every run on the fix as the baseline, whatever its label", () => {
    const fix = byId("fix-ready-archive");
    fix.check_runs = [
      { run_id: "rt", test_execution_id: "te-b1", label: "baseline-r1", state: "completed" },
      { run_id: "rt", test_execution_id: "te-b2", label: "base-2", state: "completed" },
    ];
    expect(fixRuns.checkRunsForScenarios(fix, "fix-ready-archive-c3").before.map((r) => r.test_execution_id)).toEqual([
      "te-b1",
      "te-b2",
    ]);
  });

  it("reads every page of a check run's calls", async () => {
    const pages = { 1: 200, 2: 200, 3: 37 };
    const fetchPage = vi.fn(async (page) => ({
      count: 437,
      results: Array.from({ length: pages[page] || 0 }, (_, i) => ({ id: `${page}-${i}` })),
    }));
    const rows = await fixRuns.collectAllCalls(fetchPage, 200);
    expect(rows).toHaveLength(437);
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });
});
