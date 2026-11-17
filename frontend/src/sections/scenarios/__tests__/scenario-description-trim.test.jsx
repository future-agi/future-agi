import { describe, expect, it } from "vitest";

import { CreateScenarioValidationSchema } from "../common";

// A dataset scenario built from an agent definition: the smallest shape the
// create form submits that passes every other rule.
const baseScenario = {
  sourceType: "agent_definition",
  sourceId: "agent-1",
  agentDefinitionId: "agent-1",
  agentDefinitionVersionId: "version-1",
  name: "My scenario",
  description: "Billing questions",
  customInstructionDisabled: true,
  customInstruction: "",
  noOfRows: 20,
  addPersonaAutomatically: true,
  columns: [],
  personas: [],
  kind: "dataset",
  config: { datasetId: "dataset-1" },
};

describe("CreateScenarioValidationSchema — description whitespace (#1389)", () => {
  it("trims surrounding spaces from the description", () => {
    const result = CreateScenarioValidationSchema.safeParse({
      ...baseScenario,
      description: "  hello  ",
    });

    expect(result.success).toBe(true);
    expect(result.data.description).toBe("hello");
  });

  it("turns a description that is only spaces into an empty one", () => {
    const result = CreateScenarioValidationSchema.safeParse({
      ...baseScenario,
      description: "   ",
    });

    expect(result.success).toBe(true);
    expect(result.data.description).toBe("");
  });

  it("keeps spaces inside the description", () => {
    const result = CreateScenarioValidationSchema.safeParse({
      ...baseScenario,
      description: "hello   world",
    });

    expect(result.success).toBe(true);
    expect(result.data.description).toBe("hello   world");
  });
});
