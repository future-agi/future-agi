// W1-6 (notify D40): the Data step's seeded sample reaches the create body.
import { describe, expect, it } from "vitest";
import { experimentExtras, sampleOfValues } from "../experimentFormValues";
import { sampledCount } from "src/sections/common/Runs/runLedger";
import { billsJudgeCall } from "../estimateInputs";
import { judgesOf } from "../costEstimateModel";

describe("W1-6 · sample in the create body", () => {
  it("sends sample {n, seed} only when on, with a row count, and no door selection", () => {
    expect(sampleOfValues({ sample: { on: true, n: 25, seed: 7 } })).toEqual({
      n: 25,
      seed: 7,
    });
    expect(
      sampleOfValues({ sample: { on: true, n: null, seed: 7 } }),
    ).toBeNull();
    expect(
      sampleOfValues({ sample: { on: false, n: 25, seed: 7 } }),
    ).toBeNull();
    expect(
      sampleOfValues({ sample: { on: true, n: 25, seed: 7 }, rowIds: ["a"] }),
    ).toBeNull();
    const body = experimentExtras({
      name: "x",
      sample: { on: true, n: 25, seed: 7 },
    });
    expect(body.sample).toEqual({ n: 25, seed: 7 });
    expect(experimentExtras({ name: "x" })).not.toHaveProperty("sample");
    // the estimate prices the sampled rows
    expect(
      sampledCount(
        sampleOfValues({ sample: { on: true, n: 25, seed: 7 } }),
        248,
      ),
    ).toBe(25);
    expect(sampledCount(sampleOfValues({}), 248)).toBe(248);
  });

  it("one judge-call rule: checks and code evals call no judge; others call theirs or the default", () => {
    expect(billsJudgeCall({ eval_type: "llm" })).toBe(true);
    expect(billsJudgeCall({})).toBe(true);
    expect(billsJudgeCall({ eval_type: "code" })).toBe(false);
    expect(billsJudgeCall({ evalType: "code" })).toBe(false);
    expect(billsJudgeCall({ type: "check" })).toBe(false);
    expect(
      judgesOf([{ name: "Tone" }, { name: "is_json", eval_type: "code" }]),
    ).toHaveLength(1);
  });
});
