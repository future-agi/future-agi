import { describe, it, expect } from "vitest";
import {
  fuzzyThreshold,
  levenshtein,
  splitWords,
  tokenMatchesLeaf,
  tokenizeQuery,
} from "../utils";

describe("tokenizeQuery", () => {
  it("splits on whitespace and lowercases", () => {
    expect(tokenizeQuery("  Hello World  ")).toEqual(["hello", "world"]);
  });

  it("returns an empty array for empty / nullish input", () => {
    expect(tokenizeQuery("")).toEqual([]);
    expect(tokenizeQuery(null)).toEqual([]);
    expect(tokenizeQuery(undefined)).toEqual([]);
  });

  it("drops empty tokens from repeated whitespace", () => {
    expect(tokenizeQuery("llm    gpt")).toEqual(["llm", "gpt"]);
  });
});

describe("levenshtein", () => {
  it("returns 0 for identical strings", () => {
    expect(levenshtein("foo", "foo")).toBe(0);
  });

  it("returns the other string's length when one side is empty", () => {
    expect(levenshtein("", "foo")).toBe(3);
    expect(levenshtein("foo", "")).toBe(3);
  });

  it("counts a single insertion as distance 1", () => {
    expect(levenshtein("sation", "station")).toBe(1);
  });

  it("counts a single deletion as distance 1", () => {
    expect(levenshtein("station", "sation")).toBe(1);
  });

  it("counts a single substitution as distance 1", () => {
    expect(levenshtein("cat", "bat")).toBe(1);
  });

  it("sums mixed edits (classic kitten→sitting is 3)", () => {
    expect(levenshtein("kitten", "sitting")).toBe(3);
  });

  it("counts a transposition as 2 (plain Levenshtein, not Damerau)", () => {
    expect(levenshtein("ab", "ba")).toBe(2);
  });
});

describe("fuzzyThreshold", () => {
  it("is 0 for tokens up to 3 chars (exact-only)", () => {
    expect(fuzzyThreshold("")).toBe(0);
    expect(fuzzyThreshold("a")).toBe(0);
    expect(fuzzyThreshold("ab")).toBe(0);
    expect(fuzzyThreshold("abc")).toBe(0);
  });

  it("is 1 for tokens 4-7 chars", () => {
    expect(fuzzyThreshold("abcd")).toBe(1);
    expect(fuzzyThreshold("abcdefg")).toBe(1);
  });

  it("is 2 for tokens 8+ chars (capped)", () => {
    expect(fuzzyThreshold("abcdefgh")).toBe(2);
    expect(fuzzyThreshold("abcdefghijklmnop")).toBe(2);
  });
});

describe("splitWords", () => {
  it("splits on any non-alphanumeric run", () => {
    expect(splitWords("attributes.llm.model")).toEqual([
      "attributes",
      "llm",
      "model",
    ]);
    expect(splitWords("gpt-4")).toEqual(["gpt", "4"]);
    expect(splitWords("user_id")).toEqual(["user", "id"]);
  });

  it("returns an empty array for empty / nullish input", () => {
    expect(splitWords("")).toEqual([]);
    expect(splitWords(null)).toEqual([]);
    expect(splitWords(undefined)).toEqual([]);
  });
});

describe("tokenMatchesLeaf", () => {
  it("hits via exact substring on the path", () => {
    expect(tokenMatchesLeaf("llm", "attributes.llm.model", "gpt-4", [])).toBe(
      true,
    );
  });

  it("hits via exact substring on the value", () => {
    expect(tokenMatchesLeaf("gpt", "attributes.llm.model", "gpt-4", [])).toBe(
      true,
    );
  });

  it("falls back to Levenshtein when no substring hit exists", () => {
    // `sation` isn't a substring of the path, but Levenshtein to the
    // `station` word is 1 and the threshold for 6-char tokens is 1.
    const words = splitWords("station_id");
    expect(tokenMatchesLeaf("sation", "station_id", "", words)).toBe(true);
  });

  it("does not fuzzy-match ≤ 3-char tokens", () => {
    const words = splitWords("abc");
    expect(tokenMatchesLeaf("abd", "abc", "", words)).toBe(false);
  });

  it("skips words whose length difference exceeds the threshold", () => {
    // `station` (7) vs `models` (6) — length diff 1 is within threshold 1,
    // but edit distance is > 1, so still no match.
    expect(tokenMatchesLeaf("station", "models", "", ["models"])).toBe(false);
  });

  it("handles a missing words array gracefully", () => {
    expect(tokenMatchesLeaf("sation", "station", "", undefined)).toBe(false);
  });
});
