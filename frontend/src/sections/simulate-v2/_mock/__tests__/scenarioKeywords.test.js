import { describe, expect, it } from "vitest";
import {
  keywordChoicesOf,
  keywordsOf,
  matchesKeywords,
  sourceKeywordsOf,
  withSourceKeywords,
} from "../scenarioKeywords";
import { asProposal } from "../scenarioSources";

const at = "2026-11-01T00:00:00.000Z";
const base = { addedAt: at, addedBy: { kind: "user", name: "A" } };

describe("scenario keywords", () => {
  it("gives outside sources their keywords, more than one when both are true", () => {
    expect(sourceKeywordsOf({ ...base, source: "production" })).toEqual(["from-production"]);
    expect(sourceKeywordsOf({ ...base, source: "dataset-import" })).toEqual(["from-dataset"]);
    expect(sourceKeywordsOf({ ...base, source: "transcript" })).toEqual(["from-transcript"]);
    expect(sourceKeywordsOf({ ...base, source: "improvement", errorFeedIssue: { id: "ISS-1" } })).toEqual([
      "from-issue",
      "from-production",
    ]);
    expect(sourceKeywordsOf({ ...base, traceImport: { sourceKind: "observe" } })).toEqual(["from-production"]);
  });

  it("never invents one for generated, hand-written or simulated scenarios", () => {
    ["generated", "manual", "chat", "derived", "simulation", "failing-case"].forEach((source) =>
      expect(sourceKeywordsOf({ ...base, source })).toEqual([]),
    );
    expect(sourceKeywordsOf({ ...base, source: "simulation", traceImport: { sourceKind: "simCall" } })).toEqual([]);
  });

  it("keeps keywords already there and stamps proposals", () => {
    const row = withSourceKeywords({ ...base, source: "production", keywords: ["refunds"] });
    expect(row.keywords).toEqual(["from-production", "refunds"]);
    expect(asProposal({ id: "x" }, "transcript", at).keywords).toEqual(["from-transcript"]);
    expect(withSourceKeywords({ ...base, source: "manual" }).keywords).toBeUndefined();
  });

  it("reads tag names as keywords and filters Is / Is not", () => {
    const envState = { scenarioTags: [{ id: "t1", name: "billing" }] };
    const a = { ...base, id: "a", source: "production", tagIds: ["t1"] };
    const b = { ...base, id: "b", source: "manual" };
    expect(keywordsOf(a, envState)).toEqual(["from-production", "billing"]);
    expect(keywordChoicesOf([a, b], envState).map((c) => c.label)).toEqual(["from-production (1)", "billing (1)"]);
    expect(matchesKeywords(a, envState, { is: ["from-production"] })).toBe(true);
    expect(matchesKeywords(b, envState, { is: ["from-production"] })).toBe(false);
    expect(matchesKeywords(a, envState, { isNot: ["from-production"] })).toBe(false);
    expect(matchesKeywords(b, envState, { isNot: ["from-production"] })).toBe(true);
  });
});
