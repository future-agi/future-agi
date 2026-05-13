import { describe, expect, it } from "vitest";

import {
  buildCompositeSourceModeProps,
  contextOptionsForRowType,
  extractCodeEvaluateParams,
  getSourceModeVariables,
} from "./evalPickerConfigUtils";

describe("contextOptionsForRowType", () => {
  it("maps each known row type to its default data_injection flags", () => {
    expect(contextOptionsForRowType("spans")).toEqual(["span_context"]);
    expect(contextOptionsForRowType("traces")).toEqual(["trace_context"]);
    expect(contextOptionsForRowType("sessions")).toEqual([
      "session_context",
      "trace_context",
    ]);
    expect(contextOptionsForRowType("voiceCalls")).toEqual(["call_context"]);
  });

  it("returns null for unknown / missing row types so the caller can fall back", () => {
    expect(contextOptionsForRowType(undefined)).toBeNull();
    expect(contextOptionsForRowType(null)).toBeNull();
    expect(contextOptionsForRowType("")).toBeNull();
    expect(contextOptionsForRowType("unknown")).toBeNull();
  });
});

describe("extractCodeEvaluateParams", () => {
  describe("guard clauses", () => {
    it("returns an empty array for empty / missing code", () => {
      expect(extractCodeEvaluateParams("", "python")).toEqual([]);
      expect(extractCodeEvaluateParams(null, "python")).toEqual([]);
      expect(extractCodeEvaluateParams(undefined, "python")).toEqual([]);
    });

    it("returns an empty array for unsupported languages", () => {
      const code = "def evaluate(input, output): return 0";
      expect(extractCodeEvaluateParams(code, "ruby")).toEqual([]);
      expect(extractCodeEvaluateParams(code, "go")).toEqual([]);
    });

    it("defaults to python parsing when language is falsy", () => {
      const code = "def evaluate(input, output): return 0";
      expect(extractCodeEvaluateParams(code, undefined)).toEqual([
        "input",
        "output",
      ]);
      expect(extractCodeEvaluateParams(code, "")).toEqual(["input", "output"]);
    });
  });

  describe("python", () => {
    it("parses the default template signature", () => {
      const code = `def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "output",
        "expected",
      ]);
    });

    it("includes user-added params alongside the standard ones", () => {
      const code = `def evaluate(input: Any, output: Any, expected: Any, random: Any, context: dict, **kwargs):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "output",
        "expected",
        "random",
      ]);
    });

    it("strips inline comments in multi-line signatures", () => {
      const code = `def evaluate(
    input: Any,       # Input to the AI system
    output: Any,
    expected: Any,    # Ground truth (may be None)
    context: dict,    # Mode-specific data
    **kwargs          # Additional mapped variables
):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "output",
        "expected",
      ]);
    });

    it("keeps params with nested generics intact (depth-aware split)", () => {
      const code = `def evaluate(input: Dict[str, int], output: List[Tuple[str, int]], expected: Any):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "output",
        "expected",
      ]);
    });

    it("ignores *args and **kwargs", () => {
      const code = `def evaluate(input, output, *args, **kwargs):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "output",
      ]);
    });

    it("ignores reserved params (context, self, cls)", () => {
      const code = `def evaluate(self, cls, input, context, custom):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "custom",
      ]);
    });

    it("drops type annotations and default values", () => {
      const code = `def evaluate(input: str = "x", output: int = 1, expected = None):
    return {"score": 1.0}`;
      expect(extractCodeEvaluateParams(code, "python")).toEqual([
        "input",
        "output",
        "expected",
      ]);
    });

    it("returns an empty array when there is no def evaluate", () => {
      expect(
        extractCodeEvaluateParams("def other(a, b): pass", "python"),
      ).toEqual([]);
      expect(extractCodeEvaluateParams("print('hi')", "python")).toEqual([]);
    });

    it("handles a parameter-less signature", () => {
      expect(
        extractCodeEvaluateParams("def evaluate():\n    return None", "python"),
      ).toEqual([]);
    });
  });

  describe("javascript", () => {
    it("parses the default destructured template", () => {
      const code = `function evaluate({ input, output, expected, context, ...kwargs }) {
  return { score: 1.0 };
}`;
      expect(extractCodeEvaluateParams(code, "javascript")).toEqual([
        "input",
        "output",
        "expected",
      ]);
    });

    it("includes user-added params alongside the standard ones", () => {
      const code = `function evaluate({ input, output, expected, random, context, ...kwargs }) {
  return { score: 1.0 };
}`;
      expect(extractCodeEvaluateParams(code, "javascript")).toEqual([
        "input",
        "output",
        "expected",
        "random",
      ]);
    });

    it("strips // and /* */ comments inside the destructuring", () => {
      const code = `function evaluate({
  input,    // the input
  output,   /* the output */
  expected,
  random,
  context,
  ...kwargs
}) {
  return { score: 1.0 };
}`;
      expect(extractCodeEvaluateParams(code, "javascript")).toEqual([
        "input",
        "output",
        "expected",
        "random",
      ]);
    });

    it("drops destructuring renames and default values", () => {
      const code = `function evaluate({ input, output: out, expected = null, random }) {}`;
      expect(extractCodeEvaluateParams(code, "javascript")).toEqual([
        "input",
        "output",
        "expected",
        "random",
      ]);
    });

    it("ignores ...rest params", () => {
      const code = `function evaluate({ input, output, ...rest }) {}`;
      expect(extractCodeEvaluateParams(code, "javascript")).toEqual([
        "input",
        "output",
      ]);
    });

    it("returns an empty array when not destructured", () => {
      // Parser intentionally only supports the destructured-object form
      // the JS template uses.
      const code = `function evaluate(input, output, expected) { return 0; }`;
      expect(extractCodeEvaluateParams(code, "javascript")).toEqual([]);
    });

    it("returns an empty array when there is no function evaluate", () => {
      expect(
        extractCodeEvaluateParams("function other(a) {}", "javascript"),
      ).toEqual([]);
    });
  });
});

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
