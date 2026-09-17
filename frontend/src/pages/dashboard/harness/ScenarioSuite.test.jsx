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

  it("keeps a use case with no scenarios visible so the gap is not hidden", () => {
    render(
      <ScenarioSuite
        scenarios={[scenario("one")]}
        jobId="job-1"
        editable
        useCases={["add an item to the cart", "apply a discount coupon"]}
      />,
    );
    expect(screen.getByText("apply a discount coupon")).toBeInTheDocument();
    expect(screen.getByText("not covered")).toBeInTheDocument();
    expect(screen.getByText("No scenarios were written for this use case.")).toBeInTheDocument();
  });

  it("marks a group whose use case matches none the contract declared", () => {
    render(
      <ScenarioSuite
        scenarios={[scenario("one", { use_case: "a paraphrase nobody declared" })]}
        jobId="job-1"
        editable
        useCases={["add an item to the cart"]}
      />,
    );
    expect(screen.getByText("unmatched")).toBeInTheDocument();
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
        useCases={["add an item to the cart"]}
      />,
    );
    expect(screen.getByText("two")).toBeInTheDocument();
    await user.click(screen.getByText("big mac 1"));
    expect(screen.queryByText("two")).not.toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
  });

  it("puts everything the columns cannot hold behind the row detail", async () => {
    const user = userEvent.setup();
    render(
      <ScenarioSuite
        scenarios={[
          scenario("one", {
            fixture: { name: "Ada", age: 26, origin: "mixed" },
            solution: [{ tool: "find_applicant" }, { tool: "quote_plan" }],
          }),
        ]}
        jobId="job-1"
        editable
        useCases={["add an item to the cart"]}
      />,
    );
    // Hidden until asked for, so the table stays one line per scenario.
    expect(screen.queryByText("Add one Big Mac please")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /show detail/i }));

    expect(screen.getByText("Add one Big Mac please")).toBeInTheDocument();
    expect(screen.getByText("Analytical")).toBeInTheDocument();
    expect(screen.getByText("Technical")).toBeInTheDocument();
    expect(screen.getByText("Neutral")).toBeInTheDocument();
    expect(screen.getByText("Technician")).toBeInTheDocument();
    expect(screen.getByText("1. find_applicant")).toBeInTheDocument();
    expect(screen.getByText("2. quote_plan")).toBeInTheDocument();
    // The seeded values are what make an identity edit consequential, so they are visible.
    expect(screen.getByText("Seeded into the world")).toBeInTheDocument();
  });

  it("never sends the fields the world is seeded around", async () => {
    const user = userEvent.setup();
    render(
      <ScenarioSuite
        scenarios={[scenario("one")]}
        jobId="job-1"
        editable
        useCases={["add an item to the cart"]}
      />,
    );
    await user.click(screen.getByRole("button", { name: /edit/i }));
    await user.click(screen.getByRole("button", { name: "Save" }));

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
