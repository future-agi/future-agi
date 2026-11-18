import { describe, expect, it } from "vitest";
import { designDetailPatch, scratchDesignOf, designFromSourcesPatch } from "../scratchDesign";

describe("scratchDesignOf outside the preset domains", () => {
  it("reads an issue-triage agent's actions as tools on issues", () => {
    const d = scratchDesignOf("It triages new issues: labels, assigns and closes duplicates.");
    expect(d.tools.map((t) => t.name)).toEqual(["triage_issue", "label_issue", "assign_issue", "close_issue"]);
    expect(d.tables.map((t) => t.name)).toEqual(["issues"]);
  });
  it("invents nothing from a contributor guide", () => {
    const d = scratchDesignOf("Every pull request must reference an issue. Run the service locally. Unit tests must pass.");
    expect(d.tools).toEqual([]);
  });
});

describe("designDetailPatch", () => {
  const env = { name: "Repo triage", builtFrom: { kind: "scratch", intake: { flow: "" } }, seed: { tables: [] }, tools: [], rules: [] };
  it("keeps the limit, not the capability sentence", () => {
    const res = designDetailPatch(env, "customers and their orders; it can look up an order, issue a refund up to $200");
    expect(res.envPatch.rules).toEqual(["Issue a refund up to $200"]);
  });
  it("writes cases for the tools it now knows", () => {
    const res = designDetailPatch(env, "it triages new issues: labels, assigns and closes duplicates");
    expect(res.added.cases.length).toBeGreaterThan(0);
    expect(res.envPatch.starterScenarios[0].task).toMatch(/issue/);
  });
});

describe("designFromSourcesPatch", () => {
  it("lets a file's table replace a generated one of the same name", () => {
    const env = { name: "Shop", builtFrom: {}, seed: { tables: [{ name: "orders", rows: 400 }] }, tools: [{ name: "lookup_order" }] };
    const csv = { name: "orders.csv", role: "seed", parsed: { kind: "table", table: "orders" } };
    const res = designFromSourcesPatch(env, [csv], [csv]);
    expect(res.replacedGenerated).toEqual(["orders"]);
    expect(res.envPatch.seed.tables.map((t) => t.name)).not.toContain("orders");
  });
});

describe("genericDesignOf ignores contributor lines", () => {
  it("doesn't take 'before review' in a contributing guide as a review tool", () => {
    const d = scratchDesignOf("Unit tests must pass before review. It triages new issues: labels and closes duplicates.");
    expect(d.tools.map((t) => t.name)).not.toContain("review_issue");
    expect(d.tools.map((t) => t.name)).toContain("label_issue");
  });
});
