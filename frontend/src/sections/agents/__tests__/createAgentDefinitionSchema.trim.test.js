// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import "./_setupLocalStorage";
import { createAgentDefinitionSchema } from "../helper";

// Regression guard for #1389: the agent name/description must be trimmed. An
// all-whitespace agentName is only rejected if .trim() runs before .min(1).
const schema = createAgentDefinitionSchema({ keysRequired: false });

const issueFields = (result) =>
  result.success
    ? []
    : result.error.issues.map((i) => i.path[i.path.length - 1]);

const baseAgent = {
  agentType: "text",
  languages: ["en"],
  description: "a description",
};

describe("createAgentDefinitionSchema — trims agentName (#1389)", () => {
  it("rejects an all-whitespace agentName", async () => {
    const result = await schema.safeParseAsync({
      ...baseAgent,
      agentName: "   ",
    });
    expect(issueFields(result)).toContain("agentName");
  });

  it("does not flag an agentName that is valid once trimmed", async () => {
    const result = await schema.safeParseAsync({
      ...baseAgent,
      agentName: "  Support Bot  ",
    });
    expect(issueFields(result)).not.toContain("agentName");
  });
});
