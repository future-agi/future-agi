import { describe, expect, it } from "vitest";
import { newSplitName, newSplitProblem } from "../SplitTargetControl";

const OCT_18 = Date.UTC(2026, 9, 18, 3, 0);

describe("+ New split… (IT-R1 G5 P2)", () => {
  it("defaults to prod-failures-<today> in UTC, like the experiment name", () => {
    expect(newSplitName([], OCT_18)).toBe("prod-failures-oct-18");
    expect(newSplitName([{ name: "prod-failures-oct-18" }], OCT_18)).toBe(
      "prod-failures-oct-18-2",
    );
  });

  it("explains a name it cannot use", () => {
    expect(newSplitProblem("", [])).toBe("Name the split.");
    expect(newSplitProblem("Prod Failures", [])).toMatch(/Lowercase/);
    expect(newSplitProblem("hard", [{ name: "hard" }])).toMatch(
      /already exists/,
    );
    expect(newSplitProblem("prod-failures-oct-18", [])).toBeNull();
  });
});
