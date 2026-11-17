// W6 A0: the FE seam. Empty slots add no create-body key and no Review
// cell; the entry stays the same object.
import { describe, expect, it } from "vitest";
import { CARD_EXTRA_SLOTS, cardExtrasOf, withCardExtras } from "../cardExtras";
import { RUN_USES_SLOTS, runUsesOf } from "../runUses";

describe("W6 A0 · FE seam with empty slots", () => {
  it("has the four slots in order, fill only their own function", () => {
    expect(Object.keys(CARD_EXTRA_SLOTS)).toEqual([
      "prompt",
      "agent",
      "endpoint",
      "human",
    ]);
    expect(Object.keys(RUN_USES_SLOTS)).toEqual([
      "prompt",
      "agent",
      "endpoint",
      "human",
    ]);
    // Slots fill only their own function (extrasOf / usesOf).
    Object.values(CARD_EXTRA_SLOTS).forEach((s) =>
      expect(Object.keys(s).filter((k) => k !== "extrasOf")).toEqual([]),
    );
    Object.values(RUN_USES_SLOTS).forEach((s) =>
      expect(Object.keys(s).filter((k) => k !== "usesOf")).toEqual([]),
    );
  });

  it("adds nothing", () => {
    const card = { targetType: "prompt", model: [{ value: "gpt-4o-mini" }] };
    const entry = { target_type: "prompt", model: "gpt-4o-mini" };
    expect(cardExtrasOf(card)).toEqual({});
    expect(cardExtrasOf(null)).toEqual({});
    expect(withCardExtras(entry, card)).toBe(entry);
    expect(runUsesOf(card, { kind: "prompt" })).toEqual({});
    expect(runUsesOf(null)).toEqual({});
  });
});
