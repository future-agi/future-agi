import { describe, it, expect } from "vitest";
import { createAgentDefinitionSchema } from "../helper";

// Regression tests for issue #1389:
// Agent definition name/description must be trimmed of leading/trailing
// whitespace and whitespace-only values must be rejected by the required check.
describe("createAgentDefinitionSchema whitespace handling", () => {
  const schema = createAgentDefinitionSchema();

  const validAgent = {
    agentType: "text",
    agentName: "test",
    languages: ["en"],
    description: "desc",
    inbound: false,
    commitMessage: "init",
  };

  it("trims surrounding whitespace from agentName and description", async () => {
    const result = await schema.safeParseAsync({
      ...validAgent,
      agentName: "  test  ",
      description: "  desc  ",
    });
    expect(result.success).toBe(true);
    expect(result.data.agentName).toBe("test");
    expect(result.data.description).toBe("desc");
  });

  it("preserves interior spaces in agentName", async () => {
    const result = await schema.safeParseAsync({
      ...validAgent,
      agentName: "hello world",
    });
    expect(result.success).toBe(true);
    expect(result.data.agentName).toBe("hello world");
  });

  it("rejects whitespace-only agentName", async () => {
    const result = await schema.safeParseAsync({
      ...validAgent,
      agentName: "   ",
    });
    expect(result.success).toBe(false);
  });

  it("rejects whitespace-only description", async () => {
    const result = await schema.safeParseAsync({
      ...validAgent,
      description: "   ",
    });
    expect(result.success).toBe(false);
  });
});
