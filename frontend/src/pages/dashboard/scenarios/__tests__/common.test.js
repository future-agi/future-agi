import { describe, it, expect } from "vitest";
import { LIST_ITEMS } from "../common";

// The three numbered steps on the empty Scenarios page.
describe("Scenarios empty page steps", () => {
  it("reads as plain, correct English", () => {
    expect(
      LIST_ITEMS.map(({ title, description }) => ({ title, description })),
    ).toEqual([
      {
        title: "Define Scenario Details",
        description:
          "Set the scenario name, description, agent definition, and number of scenarios you want to generate.",
      },
      {
        title: "Generate the Workflow",
        description:
          "Use the workflow builder, import a dataset, or upload scripts to create the conversation logic for your agent to follow.",
      },
      {
        title: "Add Personas and Inputs",
        description:
          "Select personas or set default persona behavior, and add custom columns to generate scenario variations.",
      },
    ]);
  });

  it("has no 'ot' typo for 'or'", () => {
    LIST_ITEMS.forEach(({ description }) =>
      expect(description).not.toMatch(/\bot\b/),
    );
  });
});
