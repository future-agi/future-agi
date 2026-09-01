import { describe, expect, it } from "vitest";

import {
  OUTPUT_TYPE_CONFIG_MAP,
  buildCompositeSourceModeProps,
  buildDataInjection,
  contextOptionsForRowType,
  extractCodeEvaluateParams,
  getSourceModeVariables,
  normalizeOutputType,
} from "./evalPickerConfigUtils";

describe("contextOptionsForRowType", () => {
  it("maps each known row type to its default data_injection flags", () => {
    expect(contextOptionsForRowType("spans")).toEqual(["span_context"]);
    expect(contextOptionsForRowType("traces")).toEqual(["trace_context"]);
    expect(contextOptionsForRowType("sessions")).toEqual(["session_context"]);
    expect(contextOptionsForRowType("voiceCalls")).toEqual(["call_context"]);
  });

  it("returns null for unknown / missing row types so the caller can fall back", () => {
    expect(contextOptionsForRowType(undefined)).toBeNull();
    expect(contextOptionsForRowType(null)).toBeNull();
    expect(contextOptionsForRowType("")).toBeNull();
    expect(contextOptionsForRowType("unknown")).toBeNull();
  });
});

describe("buildDataInjection", () => {
  it("defaults to variables_only for empty / undefined input", () => {
    expect(buildDataInjection()).toEqual({ variables_only: true });
    expect(buildDataInjection([])).toEqual({ variables_only: true });
    expect(buildDataInjection(["variables_only"])).toEqual({
      variables_only: true,
    });
  });

  it("maps each context option to its specific flag", () => {
    expect(buildDataInjection(["dataset_row"])).toEqual({ full_row: true });
    expect(buildDataInjection(["span_context"])).toEqual({
      span_context: true,
    });
    expect(buildDataInjection(["trace_context"])).toEqual({
      trace_context: true,
    });
    expect(buildDataInjection(["session_context"])).toEqual({
      session_context: true,
    });
    expect(buildDataInjection(["call_context"])).toEqual({
      call_context: true,
    });
  });

  it("accepts a legacy full_row option as an alias for dataset_row", () => {
    expect(buildDataInjection(["full_row"])).toEqual({ full_row: true });
  });

  it("combines multiple selections into a single flag dict", () => {
    expect(buildDataInjection(["span_context", "call_context"])).toEqual({
      span_context: true,
      call_context: true,
    });
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
        child_configs: {},
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

describe("normalizeOutputType", () => {
  it("converts the display form a config stores into the form the form holds", () => {
    expect(normalizeOutputType("Pass/Fail")).toBe("pass_fail");
    expect(normalizeOutputType("score")).toBe("percentage");
    expect(normalizeOutputType("choices")).toBe("deterministic");
  });

  it("maps the legacy score aliases onto percentage", () => {
    expect(normalizeOutputType("numeric")).toBe("percentage");
    expect(normalizeOutputType("reason")).toBe("percentage");
  });

  it("passes an already-normalized value through", () => {
    // A version snapshot loaded after the template column has been written
    // arrives normalized; re-mapping it would miss and fall back to Pass/Fail,
    // quietly turning a percentage eval into a pass/fail one.
    expect(normalizeOutputType("percentage")).toBe("percentage");
    expect(normalizeOutputType("deterministic")).toBe("deterministic");
  });

  it("falls back for missing or unrecognised values", () => {
    expect(normalizeOutputType(undefined)).toBe("pass_fail");
    expect(normalizeOutputType("")).toBe("pass_fail");
    expect(normalizeOutputType("nonsense")).toBe("pass_fail");
    expect(normalizeOutputType(undefined, "percentage")).toBe("percentage");
  });

  it("round-trips through the display map", () => {
    for (const normalized of Object.keys(OUTPUT_TYPE_CONFIG_MAP)) {
      expect(normalizeOutputType(OUTPUT_TYPE_CONFIG_MAP[normalized])).toBe(
        normalized,
      );
    }
  });
});
