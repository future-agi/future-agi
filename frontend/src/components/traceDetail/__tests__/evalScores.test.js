import { describe, expect, it } from "vitest";

import { getOwnEvalScores } from "../evalScores";

describe("getOwnEvalScores", () => {
  it("prefers raw scores and otherwise takes only this span from a rollup", () => {
    expect(
      getOwnEvalScores({
        observation_span: { id: "span-1" },
        eval_scores: [{ eval_config_id: "raw" }],
        eval_rollup: {
          evals: [{ spans: [{ span_id: "span-1", value: 1 }] }],
        },
      }),
    ).toEqual([{ eval_config_id: "raw" }]);

    const rollupScores = getOwnEvalScores({
      observation_span: { id: "span-1" },
      eval_rollup: {
        evals: [
          {
            config_id: "eval-1",
            spans: [
              { span_id: "span-1", value: 1 },
              { span_id: "child", value: 0 },
            ],
          },
        ],
      },
    });
    expect(rollupScores).toHaveLength(1);
    expect(rollupScores[0]).toMatchObject({
      config_id: "eval-1",
      span_id: "span-1",
      value: 1,
    });
  });
});
