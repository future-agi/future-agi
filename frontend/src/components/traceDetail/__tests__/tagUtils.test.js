import { describe, expect, it } from "vitest";
import { parseTagList } from "../tagUtils";

// TH-8026: trace-list rows can carry `tags` as the raw JSON string stored in
// ClickHouse (e.g. '["prod"]'), while drawers and span rows carry arrays.
// Callers that write tags back must know the stored list, or a tag PATCH
// (which replaces the whole list) would drop it.
describe("parseTagList", () => {
  it("returns arrays as they are", () => {
    const tags = ["prod", { name: "vip", color: "#EF4444" }];
    expect(parseTagList(tags)).toBe(tags);
  });

  it("parses a JSON-string list", () => {
    expect(parseTagList('["prod", "vip"]')).toEqual(["prod", "vip"]);
    expect(parseTagList("[]")).toEqual([]);
  });

  it("treats a missing value as no tags", () => {
    expect(parseTagList(undefined)).toEqual([]);
    expect(parseTagList(null)).toEqual([]);
    expect(parseTagList("")).toEqual([]);
  });

  it("returns null when the stored list cannot be read", () => {
    expect(parseTagList("prod")).toBeNull();
    expect(parseTagList('{"name":"prod"}')).toBeNull();
    expect(parseTagList(42)).toBeNull();
    expect(parseTagList({ name: "prod" })).toBeNull();
  });
});
