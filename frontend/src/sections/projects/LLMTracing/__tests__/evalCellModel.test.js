import { describe, expect, it } from "vitest";

import { buildEvalCellModel } from "../evalCellModel";

describe("buildEvalCellModel", () => {
  it("renders exact pass/fail count chips while hiding zero counts", () => {
    const model = buildEvalCellModel({ pass: 3, fail: 0 }, "Pass/Fail");

    expect(model.kind).toBe("counts");
    expect(model.chips).toEqual([
      expect.objectContaining({ key: "pass-0", label: "3 pass", count: 3 }),
    ]);
  });

  it("keeps numeric zero and uses 50 only as a presentation cutoff", () => {
    expect(buildEvalCellModel(0, "score")).toMatchObject({
      kind: "score",
      value: 0,
      tone: "fail",
    });
    expect(buildEvalCellModel(50, "score")).toMatchObject({ tone: "pass" });
  });

  it("normalizes scalar voice Pass/Fail labels without treating Fail as pass", () => {
    expect(buildEvalCellModel("Pass", "Pass/Fail")).toMatchObject({
      kind: "verdict",
      verdict: "pass",
    });
    expect(buildEvalCellModel("pass", "Pass/Fail")).toMatchObject({
      verdict: "pass",
    });
    expect(buildEvalCellModel("Fail", "Pass/Fail")).toMatchObject({
      verdict: "fail",
    });
  });

  it("gives duplicate choice labels unique keys and unknown choices neutral tone", () => {
    const model = buildEvalCellModel(
      { safe: 2, unexpected: 1 },
      "choices",
      { safe: "success" },
    );

    expect(model.chips.map((chip) => chip.key)).toEqual([
      "safe-0",
      "unexpected-1",
    ]);
    expect(model.chips[1]).toMatchObject({ tone: "neutral" });
  });

  it("preserves lifecycle and error markers instead of converting them to counts", () => {
    expect(buildEvalCellModel({ error: true }, "Pass/Fail")).toMatchObject({
      kind: "marker",
    });
    expect(buildEvalCellModel({ status: "pending" }, "Pass/Fail")).toMatchObject({
      kind: "marker",
    });
  });
});
