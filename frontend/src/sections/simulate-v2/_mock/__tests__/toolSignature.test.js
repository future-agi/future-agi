import { describe, expect, it } from "vitest";
import { rebuildForAgent, scenariosCalling, toolFit } from "../toolFit";

/* CN-3: an agent version that changes a required tool's signature. */
const env = {
  id: "env-sig",
  tools: [
    { name: "lookup_order", desc: "Find an order." },
    { name: "issue_refund", desc: "Refund an order." },
  ],
};
const envState = {
  agent: { values: {} },
  agentVersions: [
    { label: "v1" },
    {
      label: "v2",
      tools: [
        { name: "lookup_order", desc: "Find an order." },
        {
          name: "issue_refund",
          desc: "Refund an order.",
          params: ["order_id", "amount", "reason"],
          was: ["order_id", "amount"],
        },
      ],
    },
  ],
  scenarios: [
    { id: "s1", requiredTools: ["issue_refund"] },
    { id: "s2", requiredTools: ["lookup_order"] },
    { id: "s3", requiredTools: ["issue_refund"], proposed: { at: "x" } },
  ],
};

describe("tool signature changes", () => {
  it("v1 still fits the world it was built from", () => {
    expect(toolFit(env, envState, { agent: "v1" }).fits).toBe(true);
  });

  it("v2's changed signature breaks the pairing and blocks only its callers", () => {
    const fit = toolFit(env, envState, { agent: "v2" });
    expect(fit.fits).toBe(false);
    expect(fit.changed.map((t) => t.name)).toEqual(["issue_refund"]);
    expect(fit.changed[0].was).toEqual(["order_id", "amount"]);
    const blocked = scenariosCalling(env, envState, ["issue_refund"]);
    expect(blocked.map((r) => r.id)).toEqual(["s1"]);
  });

  it("rebuilding for v2 replaces the tool instead of adding a second one", () => {
    const { version, added } = rebuildForAgent(env, envState, "v2");
    const refunds = version.tools.filter((t) => t.name === "issue_refund");
    expect(refunds).toHaveLength(1);
    expect(refunds[0].params).toEqual(["order_id", "amount", "reason"]);
    expect(added).toHaveLength(0);
    const after = { ...envState, envVersions: [version], activeEnvVersion: version.label };
    expect(toolFit(env, after, { agent: "v2" }).fits).toBe(true);
    expect(toolFit(env, after, { agent: "v1" }).fits).toBe(true);
  });
});
