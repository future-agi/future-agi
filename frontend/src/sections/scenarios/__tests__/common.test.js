import { describe, it, expect } from "vitest";
import {
  CreateScenarioValidationSchema,
  CreateScenarioType,
  SourceType,
} from "../common";

// Regression tests for issue #1389:
// Scenario name/description must be trimmed of leading/trailing whitespace and
// a whitespace-only name must be rejected by the required check.
describe("CreateScenarioValidationSchema whitespace handling", () => {
  const validScenario = {
    kind: CreateScenarioType.GRAPH,
    sourceType: SourceType.PROMPT,
    sourceId: "src-1",
    name: "test",
    description: "desc",
    promptTemplateId: "pt-1",
    promptVersionId: "pv-1",
    customInstructionDisabled: true,
    noOfRows: 20,
    addPersonaAutomatically: true,
    columns: [],
    personas: [],
    config: { graph: {}, generateGraph: false },
  };

  it("trims surrounding whitespace from name and description", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync({
      ...validScenario,
      name: "  test  ",
      description: "  desc  ",
    });
    expect(result.success).toBe(true);
    expect(result.data.name).toBe("test");
    expect(result.data.description).toBe("desc");
  });

  it("preserves interior spaces in name", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync({
      ...validScenario,
      name: "hello world",
    });
    expect(result.success).toBe(true);
    expect(result.data.name).toBe("hello world");
  });

  it("rejects whitespace-only name", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync({
      ...validScenario,
      name: "   ",
    });
    expect(result.success).toBe(false);
  });
});
