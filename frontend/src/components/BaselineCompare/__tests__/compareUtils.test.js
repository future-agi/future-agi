import { describe, it, expect } from "vitest";
import { computeDiff, matchConversationsByIndex } from "../compareUtils";

describe("computeDiff", () => {
  it("returns an empty list when both sides are empty", () => {
    expect(computeDiff("", "")).toEqual([]);
  });

  it("marks the whole replay side as added when baseline is missing", () => {
    expect(computeDiff("", "hello there")).toEqual([
      { value: "hello there", added: true },
    ]);
  });

  it("marks the whole baseline side as removed when replay is missing", () => {
    expect(computeDiff("hello there", "")).toEqual([
      { value: "hello there", removed: true },
    ]);
  });

  it("keeps only unchanged and removed parts for the baseline column", () => {
    const parts = computeDiff("the quick fox", "the slow fox", "A");
    expect(parts.some((p) => p.added)).toBe(false);
    expect(parts.map((p) => p.value).join("")).toBe("the quick fox");
    expect(parts.filter((p) => p.removed).map((p) => p.value)).toContain(
      "quick",
    );
  });

  it("keeps only unchanged and added parts for the replay column", () => {
    const parts = computeDiff("the quick fox", "the slow fox", "B");
    expect(parts.some((p) => p.removed)).toBe(false);
    expect(parts.map((p) => p.value).join("")).toBe("the slow fox");
  });

  it("merges adjacent same-type parts into a single span", () => {
    const parts = computeDiff("one two three", "one", "A");
    const removed = parts.filter((p) => p.removed);
    expect(removed).toHaveLength(1);
    expect(removed[0].value).toBe(" two three");
  });
});

describe("matchConversationsByIndex", () => {
  const session = (contents) => ({
    conversations: contents.map((content, i) => ({ id: `t${i}`, content })),
  });

  it("pairs turns positionally", () => {
    const matched = matchConversationsByIndex(
      session(["a1", "a2"]),
      session(["b1", "b2"]),
    );
    expect(matched).toHaveLength(2);
    expect(matched[0].baseline.content).toBe("a1");
    expect(matched[0].replayed.content).toBe("b1");
  });

  it("pads the shorter side with nulls so both columns stay aligned", () => {
    const matched = matchConversationsByIndex(
      session(["a1"]),
      session(["b1", "b2", "b3"]),
    );
    expect(matched).toHaveLength(3);
    expect(matched[1].baseline).toBeNull();
    expect(matched[1].replayed.content).toBe("b2");
  });

  it("returns an empty list when either session is missing", () => {
    expect(matchConversationsByIndex(null, null)).toEqual([]);
    expect(matchConversationsByIndex(undefined, session([]))).toEqual([]);
  });
});
