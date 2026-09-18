import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

import ScenarioSuite from "./ScenarioSuite";

const amend = vi.fn();
vi.mock("src/api/harness/harness", () => ({
  amendHarnessScenarios: (...args) => amend(...args),
}));

const scenario = (name, over = {}) => ({
  name,
  use_case: "add an item to the cart",
  branch: `branch for ${name}`,
  tests: `what ${name} checks`,
  sub_goals: ["item_added"],
  background_noise: false,
  persona: {
    name: "Devon Reed",
    gender: "male",
    age_group: "25-32",
    occupation: "Technician",
    location: "United Kingdom",
    personality: "Analytical",
    communication_style: "Technical",
    accent: "Neutral",
    languages: ["English"],
    keywords: ["big mac"],
    initial_message: "Add one Big Mac please",
  },
  ...over,
});

describe("ScenarioSuite", () => {
  beforeEach(() => {
    amend.mockReset();
    amend.mockResolvedValue({ receipts: [{ outcome: "applied", why: "", scenario: "one" }] });
  });

  it("filters the suite by keyword", async () => {
    const user = userEvent.setup();
    render(
      <ScenarioSuite
        scenarios={[
          scenario("one", { persona: { ...scenario("one").persona, keywords: ["big mac"] } }),
          scenario("two", { persona: { ...scenario("two").persona, keywords: ["fries"] } }),
        ]}
        jobId="job-1"
        editable
      />,
    );
    expect(screen.getByText("two")).toBeInTheDocument();
    await user.click(screen.getByText("big mac 1"));
    expect(screen.queryByText("two")).not.toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
  });

  it("removes a single scenario from its own row", async () => {
    const user = userEvent.setup();
    render(<ScenarioSuite scenarios={[scenario("one")]} jobId="job-1" editable />);
    await user.click(screen.getByRole("button", { name: /remove from this suite/i }));
    expect(amend).toHaveBeenCalledTimes(1);
    const [, changes, options] = amend.mock.calls[0];
    expect(changes).toEqual([{ op: "drop", scenario: "one" }]);
    // Nothing is left to prove once a scenario is gone, so this never costs a rework.
    expect(options).toEqual({ rework: false });
  });

  it("searches the situation, not just the name", async () => {
    const user = userEvent.setup();
    render(
      <ScenarioSuite
        scenarios={[
          scenario("one", { instruction: "ask for a refund on a late delivery" }),
          scenario("two", { instruction: "book a table for four" }),
        ]}
        jobId="job-1"
        editable
      />,
    );
    await user.type(screen.getByPlaceholderText(/search scenarios/i), "refund");
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.queryByText("two")).not.toBeInTheDocument();
  });

  it("shows every field the design shows, and lets a person change only some", async () => {
    const user = userEvent.setup();
    render(<ScenarioSuite scenarios={[scenario("one")]} jobId="job-1" editable />);
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    // Changeable.
    expect(screen.getByRole("textbox", { name: /passes when/i })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: /branch/i })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: /personality/i })).toBeInTheDocument();

    // Shown, and not changeable: the world is seeded around these, so an edit here alone would
    // leave the persona and the world disagreeing.
    expect(screen.getByDisplayValue("Devon Reed")).toBeDisabled();
    expect(screen.getByDisplayValue("25-32")).toBeDisabled();
    expect(screen.getByDisplayValue("male")).toBeDisabled();
    expect(screen.getByDisplayValue("Add one Big Mac please")).toBeDisabled();
    expect(screen.getByDisplayValue("add an item to the cart")).toBeDisabled();
  });

  it("offers an edit even when the suite has no persona to edit", async () => {
    const user = userEvent.setup();
    const { persona, ...noCaller } = scenario("one");
    render(<ScenarioSuite scenarios={[noCaller]} jobId="job-1" editable />);

    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByLabelText(/passes when/i)).toBeInTheDocument();
    // Nobody is on the other end, so the caller section is left out rather than shown empty.
    expect(screen.queryByLabelText(/personality/i)).not.toBeInTheDocument();
  });

  it("will not spend a re-check on a form nobody changed", async () => {
    const user = userEvent.setup();
    render(<ScenarioSuite scenarios={[scenario("one")]} jobId="job-1" editable />);
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByRole("button", { name: /save scenario/i })).toBeDisabled();
  });

  it("never sends the fields the world is seeded around", async () => {
    const user = userEvent.setup();
    render(
      <ScenarioSuite
        scenarios={[scenario("one")]}
        jobId="job-1"
        editable
      />,
    );
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));
    await user.type(screen.getByLabelText(/passes when/i), " and stays polite");
    await user.click(screen.getByRole("button", { name: /save scenario/i }));

    expect(amend).toHaveBeenCalledTimes(1);
    const [, changes] = amend.mock.calls[0];
    const persona = changes.find((one) => one.op === "set_persona")?.persona || {};
    // The caller's identity is seeded into the world, so editing it here would leave the two
    // disagreeing. The form must not offer it and the payload must not carry it.
    ["name", "gender", "age_group", "initial_message"].forEach((field) =>
      expect(persona).not.toHaveProperty(field),
    );
    expect(persona).toHaveProperty("personality");
    // The use case belongs to the contract and is shared, so it is never part of an edit.
    expect(changes.some((one) => one.field === "use_case")).toBe(false);
  });
});
