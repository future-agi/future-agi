import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";
import { mapCallRow } from "src/api/simulate-environments/runCalls";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const { headline } = await import("../fixRunView");
const { scenarioOutcomes } = await import("../scenarioOutcomes");
const { default: FixFinishedView } = await import("../FixFinishedView");
const { default: TrialsTable } = await import("../TrialsTable");
const { default: FixHeader } = await import("../FixHeader");

const byId = (id) => ({
  ...structuredClone(FIX_RUN_FIXTURES.find((f) => f.id === id)),
  environment_id: "env-1",
});

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SnackbarProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );

describe("a failed fix", () => {
  const failed = () => {
    const fix = byId("fix-failed");
    fix.report = { ending: "failed", summary: "The fix did not finish." };
    fix.failure = {
      code: "engine_exited",
      message: "The engine stopped: the model provider refused the key.",
    };
    return fix;
  };

  it("leads with why it failed, not the generic summary", () => {
    expect(headline(failed())).toBe(
      "The engine stopped: the model provider refused the key.",
    );
  });

  it("shows why it failed on the finished page", () => {
    wrap(<FixFinishedView fix={failed()} tab="trials" onTab={vi.fn()} renderTab={() => null} />);
    expect(
      screen.getByText("The engine stopped: the model provider refused the key."),
    ).toBeInTheDocument();
    expect(screen.queryByText("The fix did not finish.")).toBeNull();
  });
});

describe("Per scenario on real call rows", () => {
  const row = (key, trial, outcome, harness) =>
    mapCallRow({
      id: `${key}-${trial}-${outcome}`,
      source_scenario_key: key,
      trial_index: trial,
      outcome,
      harness_outcome_status: harness,
    });

  it("counts a call the judge passed even when a platform eval made it inconclusive", () => {
    const before = [row("late-cancel", 1, "failed", "failed")];
    const after = [row("late-cancel", 1, "inconclusive", "passed")];
    expect(scenarioOutcomes(before, after).map((r) => [r.key, r.outcome])).toEqual([
      ["late-cancel", "fixed"],
    ]);
  });
});

describe("a stopped fix with a trial that was still running", () => {
  const stopped = () => {
    const fix = byId("fix-search");
    fix.state = "stopped";
    fix.stage = null;
    return fix;
  };

  it("does not offer to watch it live", () => {
    wrap(<TrialsTable fix={stopped()} />);
    const trial = screen.getByTestId("trial-2");
    expect(trial).not.toHaveTextContent("Watch live");
    expect(trial).toHaveTextContent("Did not run");
  });
});

describe("the Stop dialog", () => {
  it("does not say no trial was checked on held-back once one was", async () => {
    const fix = byId("fix-search");
    fix.candidates[1].outcome = "finalist";
    fix.candidates[1].verdict.held_back_pass_rate = [0.17, 0.83];
    const user = userEvent.setup();
    wrap(<FixHeader fix={fix} envId="env-1" />);
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.queryByText(/None of them has been checked/)).toBeNull();
    expect(screen.getByText(/Today's version stays/)).toBeInTheDocument();
  });
});
