import { describe, expect, it } from "vitest";
import { READ_AT, READ_MS, designSummary, mergeDesignFiles } from "../design";

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
  it("replaces a same-name file with new contents and skips an identical one", () => {
    const { files, note } = mergeDesignFiles(
      [file("orders.csv", 100), file("policy.md", 50)],
      [file("orders.csv", 200), file("policy.md", 50), file("faq.md")],
    );
    expect(files.map((f) => [f.name, f.size])).toEqual([
      ["orders.csv", 200],
      ["policy.md", 50],
      ["faq.md", 1000],
    ]);
    expect(note).toEqual({
      added: ["faq.md"],
      replaced: ["orders.csv"],
      skipped: ["policy.md"],
    });
  });

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

  it("blocks approval when the only file doesn't say what the agent does", () => {
    const s = designSummary(
      draft({ files: [file("CONTRIBUTING.md")], readUntil: 1 }),
      2,
    );
    expect(s.records).toEqual([]);
    expect(s.tools).toEqual([]);
    expect(s.rules).toEqual([]);
    expect(s.scenarios).toEqual([]);
    expect(s.approveBlocked).toBe(true);
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
});
