import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/_fixtures/fixRuns";

import FixRunningView from "../FixRunningView";
import TrialChart from "../TrialChart";

const byId = (id) => ({
  ...FIX_RUN_FIXTURES.find((f) => f.id === id),
  environment_id: "env-1",
});

const renderView = (fix) =>
  render(
    <MemoryRouter>
      <FixRunningView fix={fix} />
    </MemoryRouter>,
  );

describe("FixRunningView", () => {
  it("marks Search as the current step with its lines", () => {
    renderView(byId("fix-search"));
    const steps = within(screen.getByTestId("fix-steps"));
    expect(steps.getByTestId("step-search")).toHaveAttribute(
      "data-state",
      "now",
    );
    expect(steps.getByTestId("step-reproduce")).toHaveAttribute(
      "data-state",
      "done",
    );
    expect(
      steps.getByText(
        "2 of up to 4 trials · best so far 67% on 6 target scenarios",
      ),
    ).toBeInTheDocument();
    expect(steps.getByText("Starts from Run 1 on v8")).toBeInTheDocument();
  });

  it("shows what the search is held to", () => {
    renderView(byId("fix-search"));
    const held = within(screen.getByTestId("held-to"));
    expect(held.getByText("unchanged")).toBeInTheDocument();
    expect(held.getByText("1,500 credits")).toBeInTheDocument();
    expect(
      held.getByText(/A case that passed every time before/),
    ).toBeInTheDocument();
  });

  it("renders before the comparison is frozen", () => {
    const { container } = renderView(byId("fix-unknown-identity"));
    expect(screen.getByTestId("fix-steps")).toBeInTheDocument();
    expect(screen.queryByText(/of up to/)).toBeNull();
    expect(screen.queryByText(/held-back scenarios it never saw/)).toBeNull();
    expect(container.textContent).not.toMatch(/null|undefined|NaN/);
  });

  it("lists trials with what they tried, status and pass rate", () => {
    renderView(byId("fix-search"));
    const row = within(screen.getByTestId("trial-1"));
    expect(row.getByText("Trial 1")).toBeInTheDocument();
    expect(
      row.getByText(
        "Tried: Escalate refused late cancellations to the front desk.",
      ),
    ).toBeInTheDocument();
    expect(row.getByText("Rejected")).toBeInTheDocument();
    expect(row.getByText("0%")).toBeInTheDocument();
    expect(screen.getByText("Trials (2 of up to 4)")).toBeInTheDocument();
  });

  it("links a running trial to its live check run", () => {
    renderView(byId("fix-search"));
    const link = within(screen.getByTestId("trial-2")).getByRole("link", {
      name: "Running · Watch live",
    });
    expect(link).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-search/te-fix-search-c2",
    );
  });

  it("shows no per-trial metrics the contract cuts", () => {
    renderView(byId("fix-search"));
    ["Duration", "Tokens", "Said, not done", "Task success"].forEach((h) =>
      expect(screen.queryByText(h)).toBeNull(),
    );
  });
});

describe("TrialChart", () => {
  it("draws one point per trial and labels the best", () => {
    const fix = byId("fix-ready-archive");
    render(<TrialChart candidates={fix.candidates} />);
    expect(screen.getAllByTestId("trial-point")).toHaveLength(3);
    expect(
      screen.getByText("100% pass rate on the target scenarios"),
    ).toBeInTheDocument();
  });

  it("draws nothing without trials", () => {
    const { container } = render(<TrialChart candidates={[]} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
