import { describe, it, expect } from "vitest";

import { buildFromAgentPayload } from "../buildPayload";

const build = (
  source = { kind: "github", repository: "acme/agent", ref: "main" },
) =>
  buildFromAgentPayload({
    agent: { id: "agent-1" },
    version: { id: "version-2", source },
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

  it("keeps every artifact, as a build from the drawer does", () => {
    expect(build().artifacts).toEqual({ level: "full" });
  });

  it("sends a repository version's repository and ref", () => {
    expect(build().source).toEqual({
      kind: "github",
      repository: "acme/agent",
      ref: "main",
      visibility: "public",
    });
  });

  it("sends an uploaded version's archive", () => {
    expect(
      build({ kind: "archive", archive_artifact_id: "art-1" }).source,
    ).toEqual({ kind: "archive", archive_artifact_id: "art-1" });
  });

  it.each([
    ["a hosted platform", { kind: "provider" }],
    ["LiveKit", { kind: "livekit" }],
    ["a phone number", { kind: "phone" }],
    ["a version from before versioning", null],
  ])("leaves %s to the version on the platform", (_label, source) => {
    const payload = build(source);

    expect(payload.source).toEqual({ kind: "provider", visibility: "public" });
    expect(payload.agent).toEqual({
      connector: "auto",
      config: {},
      secret_refs: {},
    });
  });
});
