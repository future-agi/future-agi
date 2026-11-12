import { describe, expect, it } from "vitest";
import {
  TAG_PALETTE,
  TAG_TOKENS,
  canCreateTag,
  deleteConfirmText,
  matchTags,
  nextTagColor,
  normaliseTagName,
  tagColor,
  tagKey,
  tagNameError,
  tagsByIds,
  toggleTagAcross,
  usedOnText,
} from "../tagModel";

const TAGS = [
  { id: "t1", name: "candidate" },
  { id: "t2", name: "Support" },
  { id: "t3", name: "golden" },
];

describe("w5-c tag model", () => {
  it("has 8 palette tokens assigned in first-use order around the palette", () => {
    expect(TAG_PALETTE).toHaveLength(8);
    expect(nextTagColor(0)).toBe(TAG_TOKENS[0]);
    expect(nextTagColor(7)).toBe(TAG_TOKENS[7]);
    expect(nextTagColor(8)).toBe(TAG_TOKENS[0]);
    expect(tagColor("blue")).toBe(TAG_PALETTE[0].hex);
    expect(tagColor("nope")).toBe(TAG_PALETTE[7].hex);
  });

  it("names are trimmed, collapsed and compared case-insensitively", () => {
    expect(normaliseTagName("  release   candidate ")).toBe(
      "release candidate",
    );
    expect(tagKey("Release Candidate")).toBe("release candidate");
    expect(tagNameError("", TAGS)).toBe("Give the tag a name.");
    expect(tagNameError("SUPPORT", TAGS)).toBe(
      "A tag named “Support” already exists.",
    );
    expect(tagNameError("support", TAGS, { exceptId: "t2" })).toBeNull();
    expect(tagNameError("new", TAGS)).toBeNull();
  });

  it("search and Create tag “…”", () => {
    expect(matchTags(TAGS, "OR").map((t) => t.id)).toEqual(["t2"]);
    expect(matchTags(TAGS, "").map((t) => t.id)).toEqual(["t1", "t2", "t3"]);
    expect(canCreateTag(TAGS, "candidate")).toBe(false);
    expect(canCreateTag(TAGS, "cand")).toBe(true);
    expect(canCreateTag(TAGS, "  ")).toBe(false);
  });

  it("used-on and the delete confirmation", () => {
    expect(usedOnText({ experiments: 3, rows: 12 })).toBe(
      "3 experiments and 12 rows",
    );
    expect(usedOnText({ experiments: 1, datasets: 2, rows: 1 })).toBe(
      "1 experiment, 2 datasets and 1 row",
    );
    expect(usedOnText({})).toBe("nothing");
    expect(
      deleteConfirmText({
        name: "candidate",
        used_on: { experiments: 3, rows: 12, datasets: 0 },
      }),
    ).toBe("Removes “candidate” from 3 experiments and 12 rows");
    expect(deleteConfirmText({ name: "x", used_on: {} })).toBe(
      "Deletes “x”. Nothing uses it.",
    );
  });

  it("ids to records and toggling one tag across items", () => {
    expect(tagsByIds(TAGS, ["t3", "zz", "t1"]).map((t) => t.name)).toEqual([
      "golden",
      "candidate",
    ]);
    expect(toggleTagAcross([["t1"], []], "t1")).toEqual([["t1"], ["t1"]]);
    expect(toggleTagAcross([["t1"], ["t1", "t2"]], "t1")).toEqual([[], ["t2"]]);
    expect(toggleTagAcross([], "t1")).toEqual([]);
  });
});
