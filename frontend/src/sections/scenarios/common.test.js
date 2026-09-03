import { describe, it, expect } from "vitest";
import { CreateScenarioValidationSchema } from "./common";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimum-valid payload for the "graph" variant that satisfies all
 * cross-field .refine() rules on the discriminated union.
 */
const makeValidData = (overrides = {}) => ({
  kind: "graph",
  name: "Test Scenario",
  sourceType: "agent_definition",
  sourceId: "agent-123",
  sourceLabel: "TestAgent",
  agentDefinitionId: "agent-123",
  agentDefinitionVersionId: "version-456",
  noOfRows: 20,
  addPersonaAutomatically: true,
  columns: [],
  personas: [],
  config: { graph: null, generateGraph: true },
  customInstructionDisabled: true,
  ...overrides,
});

// ---------------------------------------------------------------------------
// Tests — name validation
// ---------------------------------------------------------------------------

describe("CreateScenarioValidationSchema — name validation", () => {
  it("rejects an empty name via min(1)", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "" }),
    );
    expect(result.success).toBe(false);
    const messages = result.error.issues.map((i) => i.message);
    expect(messages).toContain("Name is required");
  });

  it("rejects whitespace-only names", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "   " }),
    );
    expect(result.success).toBe(false);
    const messages = result.error.issues.map((i) => i.message);
    expect(messages).toContain("Name is required");
  });

  it("accepts a valid name", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "My Test Scenario" }),
    );
    expect(result.success).toBe(true);
  });

  it("trims whitespace from the name", async () => {
    const result = await CreateScenarioValidationSchema.safeParseAsync(
      makeValidData({ name: "  Padded Name  " }),
    );
    expect(result.success).toBe(true);
    expect(result.data.name).toBe("Padded Name");
  });
});
