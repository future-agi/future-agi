import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { render } from "src/utils/test-utils";

import ScenarioOutcome from "./ScenarioOutcome";

const failing = {
  status: "failed",
  turns: 13,
  durationMs: 97560,
  subGoals: [
    { name: "ride_booked", held: false, reason: "book_ride never succeeded" },
    { name: "address_confirmed", held: true, reason: null },
  ],
};

describe("ScenarioOutcome", () => {
  it("says how the scenario went and how long its call ran", () => {
    render(<ScenarioOutcome outcome={failing} />);
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("13 turns · 1m 37s")).toBeInTheDocument();
  });

  // The reason a scenario failed is the point of the row, but it is a paragraph per goal —
  // kept behind a toggle so ten scenarios still read as a list.
  it("keeps the failed-check reasons behind a toggle", async () => {
    const user = userEvent.setup();
    render(<ScenarioOutcome outcome={failing} />);

    expect(screen.queryByText(/book_ride never succeeded/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /1 of 2 checks failed/ }));
    expect(screen.getByText(/book_ride never succeeded/)).toBeInTheDocument();
  });

  it("lists only the goals that did not hold", async () => {
    const user = userEvent.setup();
    render(<ScenarioOutcome outcome={failing} />);
    await user.click(screen.getByRole("button", { name: /1 of 2 checks failed/ }));
    expect(screen.getByText("ride_booked")).toBeInTheDocument();
    expect(screen.queryByText("address_confirmed")).not.toBeInTheDocument();
  });

  it("offers nothing to open when every goal held", () => {
    render(
      <ScenarioOutcome
        outcome={{
          status: "passed",
          turns: 11,
          durationMs: 106000,
          subGoals: [{ name: "suspension_refused_with_handoff", held: true, reason: null }],
        }}
      />,
    );
    expect(screen.getByText("passed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /checks failed/ })).not.toBeInTheDocument();
  });

  // A scenario still running, or any sandbox run, reports no outcome at all.
  it("renders nothing without an outcome", () => {
    const { container } = render(<ScenarioOutcome outcome={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("survives a scenario whose call was never measured", () => {
    render(<ScenarioOutcome outcome={{ status: "failed", turns: null, durationMs: null, subGoals: [] }} />);
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.queryByText(/turns/)).not.toBeInTheDocument();
  });

  it("renders nothing when the outcome carries nothing to show", () => {
    const { container } = render(
      <ScenarioOutcome outcome={{ status: null, turns: null, durationMs: null, subGoals: [] }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
