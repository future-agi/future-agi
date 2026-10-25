import { describe, expect, it } from "vitest";
import { memberPathOf, memberResultsOf } from "../memberAttribution";

const squad = {
  definition: {
    members: [
      { id: "greeter", name: "Billing greeter", role: "Entry · routes the caller" },
      { id: "payments", name: "Payments specialist" },
      { id: "disputes", name: "Disputes specialist" },
    ],
  },
};
const tasks = Array.from({ length: 30 }, (_, i) => ({
  id: `t${i}`,
  scenarioId: `s${i}`,
  status: i % 3 === 0 ? "failed" : "passed",
}));

describe("memberResultsOf", () => {
  it("returns null for a single agent", () => {
    expect(memberResultsOf({ agent: {}, tasks })).toBeNull();
  });

  it("starts every conversation at the entry member", () => {
    const r = memberResultsOf({ agent: squad, tasks });
    const entry = r.rows.find((x) => x.entry);
    expect(entry.member.id).toBe("greeter");
    expect(entry.reached).toBe(30);
  });

  it("credits each conversation to exactly one member, and counts handoffs", () => {
    const r = memberResultsOf({ agent: squad, tasks, causeOf: () => "Refused a valid refund" });
    expect(r.rows.reduce((n, x) => n + x.ended, 0)).toBe(30);
    expect(r.rows.reduce((n, x) => n + x.passed + x.failed, 0)).toBe(30);
    expect(r.rows.find((x) => x.entry).handedOff).toBe(r.handoffs);
    const withFailures = r.rows.find((x) => x.failed > 0);
    expect(withFailures.topCause.label).toBe("Refused a valid refund");
  });

  it("follows a scenario that names its member", () => {
    const path = memberPathOf({ id: "x", member: "disputes" }, squad.definition.members);
    expect(path.map((m) => m.id)).toEqual(["greeter", "disputes"]);
  });

  it("is stable for the same run", () => {
    const a = memberResultsOf({ agent: squad, tasks });
    const b = memberResultsOf({ agent: squad, tasks });
    expect(a.rows.map((x) => x.ended)).toEqual(b.rows.map((x) => x.ended));
  });
});
