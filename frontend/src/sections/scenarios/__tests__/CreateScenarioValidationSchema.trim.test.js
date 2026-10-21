import { describe, it, expect } from "vitest";
import { CreateScenarioValidationSchema } from "../common";

// Regression guard for #1389: the scenario name must be trimmed so a value like
// " test " is not persisted verbatim (creating near-duplicates), and an
// all-whitespace name is rejected instead of passing .min(1) on its raw length.
const issueFields = (result) =>
  result.success ? [] : result.error.issues.map((i) => i.path[i.path.length - 1]);

// A fully valid DATASET scenario (satisfies the source/instruction refinements)
// so we can assert the transformed output, not just field-level validity.
const validDatasetScenario = {
  kind: "dataset",
  sourceType: "agent_definition",
  sourceId: "source-1",
  agentDefinitionId: "ad-1",
  agentDefinitionVersionId: "adv-1",
  customInstructionDisabled: true,
  noOfRows: 10,
  addPersonaAutomatically: false,
  columns: [],
  personas: [{ id: "persona-1" }],
  config: { datasetId: "dataset-1" },
};

describe("CreateScenarioValidationSchema — trims name (#1389)", () => {
  it("trims the surrounding whitespace from name and description", () => {
    const parsed = CreateScenarioValidationSchema.parse({
      ...validDatasetScenario,
      name: "  My scenario  ",
      description: "  a description  ",
    });
    expect(parsed.name).toBe("My scenario");
    expect(parsed.description).toBe("a description");
  });

  it("rejects an all-whitespace name (trim runs before .min(1))", () => {
    // Without .trim(), "   " (length 3) would satisfy .min(1); trimming first
    // reduces it to "" so it is correctly rejected.
    const result = CreateScenarioValidationSchema.safeParse({
      ...validDatasetScenario,
      name: "   ",
    });
    expect(result.success).toBe(false);
    expect(issueFields(result)).toContain("name");
  });
});
