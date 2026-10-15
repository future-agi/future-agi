import { describe, expect, it } from "vitest";
import { getEvalTags } from "../evalTags";

describe("getEvalTags", () => {
  it("reads template-level tags from the eval detail payload", () => {
    expect(getEvalTags({ tags: ["SAFETY", "RAG"] })).toEqual(["SAFETY", "RAG"]);
  });

  it("falls back to eval_tags for legacy payloads", () => {
    expect(getEvalTags({ eval_tags: ["LEGACY"] })).toEqual(["LEGACY"]);
  });

  it("returns an empty list when tags are missing", () => {
    expect(getEvalTags(null)).toEqual([]);
    expect(getEvalTags({})).toEqual([]);
  });
});
