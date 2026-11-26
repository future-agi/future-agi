import { describe, expect, it } from "vitest";
import { READ_AT, READ_MS, designSummary, untitledNameOf } from "../design";

const file = (name, size = 1000) => ({
  id: `${name}-${size}`,
  name,
  size,
  type: "",
});
const draft = (extra) => ({
  name: "Refunds",
  description: "",
  files: [],
  readUntil: 0,
  ...extra,
});

describe("design", () => {
  it("hides each part until its read time", () => {
    const d = draft({
      files: [file("orders.csv"), file("refund_policy.md")],
      readUntil: 10_000 + READ_MS,
    });
    const at = (share) => designSummary(d, 10_000 + share * READ_MS);

    const start = at(0);
    expect(start.readingParts).toEqual(Object.keys(READ_AT));
    expect(start.records).toBeNull();

    const mid = at(READ_AT.tools);
    expect(mid.records).not.toBeNull();
    expect(mid.tools).not.toBeNull();
    expect(mid.rules).toBeNull();
    expect(mid.readingParts).toEqual(["rules", "people", "scenarios"]);

    const done = at(1);
    expect(done.readingParts).toEqual([]);
    expect(done.scenarios.length).toBeGreaterThan(0);
    expect(done.approveBlocked).toBe(false);
  });

  it("blocks approval only when there is nothing to build from", () => {
    const at = (extra) => designSummary(draft({ readUntil: 1, ...extra }), 2);
    const noTools = at({ files: [file("CONTRIBUTING.md")] });
    expect(noTools.tools).toEqual([]);
    expect(noTools.approveBlocked).toBe(false);
    expect(at({ description: "It helps people." }).approveBlocked).toBe(true);
    expect(at({ files: [file("notes.pages")] }).approveBlocked).toBe(true);
  });

  it("offers a stand-in for what can't be built and adds it once simulated", () => {
    const d = draft({ description: "A customer emails a refund request." });
    expect(designSummary(d, 1).deps.map((x) => [x.id, x.choice])).toEqual([
      ["email", null],
    ]);
    const simulated = designSummary(
      { ...d, depChoices: { email: "simulate" } },
      1,
    );
    expect(simulated.tools.map((t) => t.name)).toContain("email_outbox");
  });

  it("numbers an untitled name only on a clash, and suggests one once read", () => {
    expect(untitledNameOf(["Refund desk"])).toBe("Untitled environment");
    expect(
      untitledNameOf(["untitled environment", "Untitled environment 2"]),
    ).toBe("Untitled environment 3");
    const d = draft({
      description: "A customer asks for a refund on a late order.",
      files: [file("orders.csv")],
      readUntil: 10_000 + READ_MS,
    });
    expect(designSummary(d, 10_000).suggestedName).toBeNull();
    expect(designSummary(d, 10_000 + READ_MS).suggestedName).toBe(
      "Refund desk",
    );
  });
});
