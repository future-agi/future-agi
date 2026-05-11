import { describe, expect, it } from "vitest";

import {
  buildCompositeSourceModeProps,
  getSourceModeVariables,
} from "./evalPickerConfigUtils";

describe("buildCompositeSourceModeProps", () => {
  it("does not expose adhoc config for non-composite evals", () => {
    expect(
      buildCompositeSourceModeProps({
        isComposite: false,
      }),
    ).toEqual({ isComposite: false });
  });

  it("builds and forwards composite adhoc config from current composite detail and weights", () => {
    expect(
      buildCompositeSourceModeProps({
        isComposite: true,
        fullEval: {
          aggregation_enabled: true,
          aggregation_function: "weighted_avg",
          composite_child_axis: "pass_fail",
          pass_threshold: 0.5,
        },
        compositeDetail: {
          aggregation_enabled: false,
          aggregation_function: "min",
          composite_child_axis: "percentage",
          pass_threshold: 0.7,
          children: [
            { child_id: "child-a", weight: 1.5 },
            { child_id: "child-b", weight: 2 },
          ],
        },
        compositeChildWeights: {
          "child-a": 3,
        },
      }),
    ).toEqual({
      isComposite: true,
      compositeAdhocConfig: {
        child_template_ids: ["child-a", "child-b"],
        aggregation_enabled: false,
        aggregation_function: "min",
        composite_child_axis: "percentage",
        child_weights: {
          "child-a": 3,
          "child-b": 2,
        },
        pass_threshold: 0.7,
      },
    });
  });
});

describe("getSourceModeVariables", () => {
  it("returns base variables for non-composite evals", () => {
    expect(
      getSourceModeVariables({
        isComposite: false,
        variables: ["input"],
        compositeUnionKeys: ["child_input"],
      }),
    ).toEqual(["input"]);
  });

  it("returns composite union keys for composite evals", () => {
    expect(
      getSourceModeVariables({
        isComposite: true,
        variables: ["input"],
        compositeUnionKeys: ["child_input", "child_output"],
      }),
    ).toEqual(["child_input", "child_output"]);
  });
});
