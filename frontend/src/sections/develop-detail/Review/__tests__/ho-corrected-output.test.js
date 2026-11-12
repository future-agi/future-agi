// HO results R4: a reviewer's corrected output on a result (pure parts).
import { describe, expect, it } from "vitest";
import {
  correctedByLine,
  correctedOf,
  correctedRunsOf,
  correctionDiffers,
} from "../correctedOutputModel";

const c = {
  value: "Refund issued.",
  by: { id: "u1", name: "Alice Park" },
  at: "2026-10-20T10:00:00Z",
};

describe("corrected output", () => {
  it("reads only a non-empty correction", () => {
    expect(correctedOf({ corrected_output: c })).toBe(c);
    expect(correctedOf({ corrected_output: { value: "  " } })).toBeNull();
    expect(correctedOf({})).toBeNull();
  });
  it("names who and when", () => {
    expect(
      correctedByLine(c, { now: Date.parse("2026-10-23T10:00:00Z") }),
    ).toMatch(/^Corrected by Alice Park · /);
    expect(correctedByLine({ value: "x" })).toBe("Corrected");
  });
  it("knows when it differs from the output, and which runs carry one", () => {
    expect(correctionDiffers(c, "Refund issued. ")).toBe(false);
    expect(correctionDiffers(c, "No refund.")).toBe(true);
    const row = { results: { a: {}, b: { corrected_output: c } } };
    expect(
      correctedRunsOf(row, [{ run_id: "a" }, { run_id: "b" }]).map(
        (x) => x.run.run_id,
      ),
    ).toEqual(["b"]);
  });
});
