// HO-P1 (integration from the HO shell, S8): agentLinkOf opens the version the
// run ran. A Test a fix child copies its parent's target (v3) and names the
// version it saved (v3.1) in its config; the link opens v3.1, the version
// Publish activates (iterate/publishGate.js publishVersionIdOf).
import { describe, expect, it } from "vitest";
import { agentLinkOf } from "../targetLinks";
import { publishVersionIdOf } from "src/sections/experiment-detail/iterate/publishGate";

const target = {
  graph_id: "g-support",
  version_id: "gv-3",
  version_name: "v3",
};
const child = {
  target,
  config: { agent: { id: "g-support", version_id: "gv-3-1", version: "v3.1" } },
};

describe("agentLinkOf (HO-P1)", () => {
  it("prefers the config's version over the target's", () => {
    expect(agentLinkOf(child.target, { config: child.config })).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3-1",
    );
    expect(agentLinkOf(child.target, { config: child.config })).toContain(
      publishVersionIdOf(child),
    );
  });

  it("falls back to the target's version when the config names none", () => {
    expect(agentLinkOf(target, { config: { agent: {} } })).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3",
    );
    expect(agentLinkOf(target)).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3",
    );
  });
});
