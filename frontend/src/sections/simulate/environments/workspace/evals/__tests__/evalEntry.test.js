import { describe, it, expect } from "vitest";
import { costLabel, inputRowsOf, sourceLabel } from "../evalEntry";
import { CODE_EVAL, CUSTOM_EVAL, NO_MISSELLING } from "./fixtures/evalEntries";

describe("evalEntry — the entry, read not computed", () => {
  it("reads Library/Custom off `source`", () => {
    expect(sourceLabel(NO_MISSELLING)).toBe("Library");
    expect(sourceLabel(CUSTOM_EVAL)).toBe("Custom");
  });

  // Both fields are optional. "Library" is a definite claim about where an
  // eval came from; a price is a definite claim about what it costs. Neither
  // may be manufactured out of an absent field — the caller draws no chip.
  it("says nothing about a source the entry does not carry", () => {
    expect(sourceLabel({})).toBeNull();
    expect(sourceLabel(undefined)).toBeNull();
    expect(sourceLabel({ source: null })).toBeNull();
    // Specifically: never the definite "Library".
    expect(sourceLabel({ name: "no_misselling" })).not.toBe("Library");
  });

  it("names no price when the entry carries no `credits_per_run`", () => {
    expect(costLabel({})).toBeNull();
    expect(costLabel(undefined)).toBeNull();
    expect(costLabel({ charges_judge_tokens: true }, true)).toBeNull();
    // Specifically: never the 0.5 the catalogue happens to charge today.
    expect(costLabel({ name: "no_misselling" })).not.toBe("0.5 credits per run");
    expect(costLabel({ credits_per_run: null })).toBeNull();
  });

  it("builds the cost line from `credits_per_run` and `charges_judge_tokens`", () => {
    expect(costLabel(NO_MISSELLING)).toBe("0.5 credits per run + judge tokens");
    expect(costLabel(CODE_EVAL)).toBe("0.5 credits per run");
  });

  it("names a real zero price rather than dropping the chip", () => {
    // 0 is a number the API can genuinely send, and it is a different answer
    // from "no price was sent" — it must survive the absent-field guard.
    expect(costLabel({ credits_per_run: 0 })).toBe("0 credits per run");
  });

  it("swaps the cost line for the run-mode chip when opened from a run", () => {
    // On the run screen "run" already means the simulation run, so the chip
    // reads "per call graded" instead of "per run" — same two numbers
    // (`credits_per_run`, `charges_judge_tokens`), different words.
    expect(costLabel(NO_MISSELLING, true)).toBe("0.5 credits per call graded + judge tokens");
    expect(costLabel(CODE_EVAL, true)).toBe("0.5 credits per call graded");
    // runMode defaults to false — the Evaluations tab's call sites are unaffected.
    expect(costLabel(NO_MISSELLING)).toBe(costLabel(NO_MISSELLING, false));
  });

  it("reads singular when the API ever sends exactly 1 credit", () => {
    expect(costLabel({ credits_per_run: 1 })).toBe("1 credit per run");
    expect(costLabel({ credits_per_run: 1 }, true)).toBe("1 credit per call graded");
    // Still plural either side of 1.
    expect(costLabel({ credits_per_run: 2 })).toBe("2 credits per run");
  });

  it("returns `inputs` exactly as the API built them", () => {
    expect(inputRowsOf(NO_MISSELLING)).toEqual(NO_MISSELLING.inputs);
  });

  it("renders a source it has never heard of, because the label comes from the API", () => {
    const rows = inputRowsOf({
      inputs: [{ key: "whatever", source: "some_new_source", label: "Something new" }],
    });
    expect(rows[0].label).toBe("Something new");
  });

  it("treats a missing or empty `inputs` as no arrows, never as an error", () => {
    expect(inputRowsOf({})).toEqual([]);
    expect(inputRowsOf(undefined)).toEqual([]);
  });
});
