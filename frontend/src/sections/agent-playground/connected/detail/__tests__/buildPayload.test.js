import { describe, it, expect } from "vitest";

import { buildFromAgentPayload } from "../buildPayload";

const build = () =>
  buildFromAgentPayload({
    agent: { id: "agent-1" },
    version: {
      id: "version-2",
      source: { kind: "github", repository: "acme/agent", ref: "main" },
    },
    name: "support-env",
    scenarioCount: 10,
  });

describe("buildFromAgentPayload", () => {
  it("builds from the chosen version without asking to follow production", () => {
    const { metadata } = build();

    expect(metadata).toEqual({
      name: "support-env",
      authoring_key: "support-env",
      agent_definition_id: "agent-1",
      agent_version_id: "version-2",
    });
    expect(metadata).not.toHaveProperty("agent_policy");
  });
});
