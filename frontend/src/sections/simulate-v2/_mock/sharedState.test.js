import { describe, it, expect } from "vitest";
import { sharedStateOf } from "./sharedState";

const members = [
  { id: "front", name: "Front desk", tools: ["lookup_order"] },
  { id: "refunds", name: "Refunds specialist", tools: ["issue_refund"] },
  { id: "esc", name: "Escalations", tools: ["escalate_to_human"] },
];
const agent = {
  sourceKind: "platform",
  definition: { kind: "platform", members },
};
const hits = Array.from({ length: 60 }, (_, i) => ({
  id: `t${i}`,
  status: "failed",
}))
  .map((task) => ({ task, s: sharedStateOf({ agent, task }) }))
  .filter((x) => x.s);

describe("sharedStateOf", () => {
  it("shows for some multi-agent trials, with two writers of one record", () => {
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0].s.rows).toHaveLength(2);
    expect(hits[0].s.rows[0].result).toBe("Committed");
    expect(hits[0].s.final).toBe("fail");
  });
  it("is null for a single agent", () => {
    expect(
      sharedStateOf({
        agent: { definition: { members: [members[0]] } },
        task: hits[0].task,
      }),
    ).toBeNull();
  });
  it("reads Unknown when the participant was not observed", () => {
    const blind = { ...agent, values: { delegationEvents: false } };
    const x = sharedStateOf({ agent: blind, task: hits[0].task });
    expect(x.rows[1].participant).toBe("Unknown");
  });
});
