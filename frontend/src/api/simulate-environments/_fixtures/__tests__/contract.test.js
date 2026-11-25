import { describe, it, expect } from "vitest";
import { contractFor, subTasksFor, describeUseCase } from "../contract";

const env = {
  id: "env-1",
  name: "Voice support",
  surface: "voice",
  tools: [
    { name: "lookup_account", desc: "reads the caller's account", args: ["caller_id"] },
    { name: "issue_refund", desc: "refunds a charge", args: ["charge_id"] },
  ],
  rules: ["Verify the caller before any account change."],
  seed: { tables: [{ name: "accounts" }, { name: "charges" }] },
};

describe("contract", () => {
  it("depends on postgres and the surface service", () => {
    const contract = contractFor(env);
    const names = contract.dependsOn.map((d) => d.name);
    expect(names).toContain("postgres");
    expect(names).toContain("livekit-sip");
  });

  it("derives at least one sub-task for a scenario", () => {
    const subs = subTasksFor({ id: "env-1-core-1", title: "Routine task" }, env);
    expect(subs.length).toBeGreaterThanOrEqual(1);
    expect(subs[0]).toHaveProperty("id");
    expect(subs[0]).toHaveProperty("label");
  });

  it("labels a use case from the scenario id", () => {
    expect(describeUseCase({ id: "env-1-rule-2" }).kind).toBe("rule");
  });
});
