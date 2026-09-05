import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { render } from "src/utils/test-utils";

import ScenarioCard from "./ScenarioCard";

const richScenario = {
  name: "dispute_double_charge",
  scenario_key: "dispute_double_charge",
  instruction: "A customer says they were charged twice and wants a refund.",
  use_case: "Billing dispute",
  status: "failed",
  goal: "Verify the duplicate charge before offering a refund.",
  sub_goals: [
    { name: "verify_identity", description: "Confirm the account", held: true },
    {
      name: "locate_both_charges",
      description: "Find the two transactions",
      held: false,
      reason: "The agent never looked up the second charge.",
    },
  ],
  persona: {
    name: "Frustrated regular",
    role: "Long-time customer",
    situation: "Low patience for scripted answers.",
    traits: { mood: "Angry" },
  },
  background_noise: ["Interrupts before the agent finishes"],
  actors: [
    {
      name: "Billing system",
      role: "Holds the transaction record",
      sub_actors: [{ name: "Ledger", role: "Stores each charge" }],
    },
  ],
  variables: { order_id: "RB-8841", amount: "$42.50" },
};

describe("ScenarioCard", () => {
  it("shows every populated section of a rich scenario when expanded", () => {
    render(<ScenarioCard scenario={richScenario} defaultExpanded />);

    // The name and its collapsed-row chips. The persona name appears both as a summary chip
    // and inside the expanded metadata panel, so it is expected more than once.
    expect(screen.getByText("dispute_double_charge")).toBeVisible();
    expect(screen.getAllByText("Frustrated regular").length).toBeGreaterThan(0);
    expect(screen.getByText("Long-time customer")).toBeVisible();
    expect(screen.getByText(/2 sub-goals/)).toBeVisible();

    // The prompt and success criterion are distinct, labelled sections.
    expect(screen.getByText("Prompt")).toBeVisible();
    expect(screen.getByText("Success criterion")).toBeVisible();
    expect(
      screen.getByText(/Verify the duplicate charge before offering a refund/),
    ).toBeVisible();

    // Sub-goals, and the failed one carries its reason.
    expect(screen.getByText("locate_both_charges")).toBeVisible();
    expect(
      screen.getByText(/The agent never looked up the second charge/),
    ).toBeVisible();

    // The metadata panel: persona, background noise, actors + sub-actors, variables.
    expect(screen.getByText("Background noise")).toBeVisible();
    expect(
      screen.getByText(/Interrupts before the agent finishes/),
    ).toBeVisible();
    expect(screen.getByText("Billing system")).toBeVisible();
    expect(screen.getByText("Ledger")).toBeVisible();
    expect(screen.getByText("order_id")).toBeVisible();
    expect(screen.getByText("RB-8841")).toBeVisible();
  });

  it("renders a minimal scenario without inventing sections it has no data for", () => {
    render(
      <ScenarioCard
        scenario={{
          name: "minimal_smalltalk",
          instruction: "Greet the assistant.",
          use_case: "Warm-up",
        }}
        defaultExpanded
      />,
    );

    expect(screen.getByText("minimal_smalltalk")).toBeVisible();
    expect(screen.getByText("Greet the assistant.")).toBeVisible();
    expect(screen.getByText("Warm-up")).toBeVisible();

    // No goal, persona, sub-goals, or variables were provided, so those sections are absent
    // entirely rather than rendered empty.
    expect(screen.queryByText("Success criterion")).toBeNull();
    expect(screen.queryByText("Sub-goals")).toBeNull();
    expect(screen.queryByText("Background noise")).toBeNull();
    expect(screen.queryByText("Variables")).toBeNull();
  });

  it("reveals the raw scenario JSON on demand", async () => {
    const user = userEvent.setup();
    render(<ScenarioCard scenario={richScenario} defaultExpanded />);

    // The raw payload is collapsed until asked for.
    expect(screen.queryByText(/"scenario_key"/)).toBeNull();
    await user.click(screen.getByText("View raw scenario"));
    expect(screen.getByText(/"scenario_key"/)).toBeVisible();
  });
});
