import { describe, expect, it } from "vitest";
import { autoPatch, capError, draftFromAuto, draftsEqual } from "./autoRouting";

describe("draftFromAuto", () => {
  it("defaults to balanced with no candidates or caps", () => {
    expect(draftFromAuto(undefined)).toEqual({
      preference: "balanced",
      candidates: [],
      maxInput: "",
      maxOutput: "",
    });
  });

  it("reads saved values and ignores ones the gateway would ignore", () => {
    expect(
      draftFromAuto({
        preference: "quality",
        candidates: ["a/b", "", 7, "c/d"],
        max_input_per_mtok: 5,
        max_output_per_mtok: 0,
      }),
    ).toEqual({
      preference: "quality",
      candidates: ["a/b", "c/d"],
      maxInput: "5",
      maxOutput: "",
    });
    expect(draftFromAuto({ preference: "fastest" }).preference).toBe(
      "balanced",
    );
  });
});

describe("capError", () => {
  it("accepts blank and positive amounts", () => {
    expect(capError("")).toBe("");
    expect(capError(" 2.5 ")).toBe("");
  });

  it("rejects zero, negatives and text", () => {
    expect(capError("0")).toMatch(/above \$0/);
    expect(capError("-1")).toMatch(/above \$0/);
    expect(capError("five")).toMatch(/above \$0/);
  });
});

describe("autoPatch", () => {
  const draft = (overrides) => ({
    preference: "balanced",
    candidates: [],
    maxInput: "",
    maxOutput: "",
    ...overrides,
  });

  it("sends set values as numbers and lists", () => {
    expect(
      autoPatch(
        draft({
          preference: "cost",
          candidates: ["a/b"],
          maxInput: "3",
          maxOutput: " 15 ",
        }),
        undefined,
      ),
    ).toEqual({
      preference: "cost",
      candidates: ["a/b"],
      max_input_per_mtok: 3,
      max_output_per_mtok: 15,
    });
  });

  it("never sends null for a key the saved config does not have", () => {
    expect(autoPatch(draft({ preference: "quality" }), undefined)).toEqual({
      preference: "quality",
    });
    expect(autoPatch(draft(), { preference: "cost" })).toEqual({
      preference: "balanced",
    });
  });

  it("clears a saved key with null", () => {
    expect(
      autoPatch(draft(), {
        preference: "cost",
        candidates: ["a/b"],
        max_input_per_mtok: 3,
      }),
    ).toEqual({
      preference: "balanced",
      candidates: null,
      max_input_per_mtok: null,
    });
  });
});

describe("draftsEqual", () => {
  it("ignores surrounding whitespace in caps but not candidate order", () => {
    const a = draftFromAuto({ candidates: ["x", "y"], max_input_per_mtok: 2 });
    expect(draftsEqual(a, { ...a, maxInput: " 2 " })).toBe(true);
    expect(draftsEqual(a, { ...a, candidates: ["y", "x"] })).toBe(false);
  });
});
