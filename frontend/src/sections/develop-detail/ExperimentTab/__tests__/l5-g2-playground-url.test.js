// options-truth L5 G2: Open in Agent Playground lands on the builder of the
// run's own graph version (the playground reads `?version=<version id>`).
import { describe, expect, it } from "vitest";
import { agentPlaygroundUrl } from "../RunRowMenu";

describe("agentPlaygroundUrl", () => {
  it("names the run's graph version id on the build tab", () => {
    expect(
      agentPlaygroundUrl({
        target: { graph_id: "g1", version_id: "v2-id", version_name: "v2" },
      }),
    ).toBe("/dashboard/agents/playground/g1/build?version=v2-id");
    expect(
      agentPlaygroundUrl({
        config: {
          agent: { graph_id: "g1", version_id: "v3-id", version: "v3" },
        },
      }),
    ).toBe("/dashboard/agents/playground/g1/build?version=v3-id");
    expect(agentPlaygroundUrl({ target: { graph_id: "g1" } })).toBe(
      "/dashboard/agents/playground/g1/build",
    );
    expect(agentPlaygroundUrl({})).toBeNull();
  });
});
