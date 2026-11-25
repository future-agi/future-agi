import { describe, expect, it } from "vitest";
import {
  getDecisionRunConfigOverrides,
  sanitizeEvalMapping,
  serializeEvalConfig,
} from "./serializeEvalConfig";

describe("serializeEvalConfig", () => {
  it("preserves the decision connection and typed mapping through binding serialization", () => {
    const model = "decision/openrouter/openai/gpt-6-luna-decisions";
    const jev_mapping = {
      revision: "jev-map-v1",
      question_type: "score",
      score: { levels: ["Incorrect", "Correct"] },
    };
    const serialized = serializeEvalConfig({
      templateId: "eval",
      model,
      jev_mapping,
      config: {
        jev_mapping,
        run_config: { output_type: "score", pass_threshold: 0 },
      },
    });
    expect(serialized).toMatchObject({
      model,
      config: {
        jev_mapping,
        run_config: {
          model,
          jev_mapping,
          output_type: "score",
          pass_threshold: 0,
        },
      },
    });
    expect(serialized).not.toHaveProperty("jev_mapping");
  });

  it("emits runtime overrides only inside config.run_config", () => {
    const payload = serializeEvalConfig({
      templateId: "template-1",
      name: "quality_check",
      model: "turing_large",
      mapping: { output: "answer" },
      pass_threshold: 0.8,
      check_internet: true,
      knowledge_bases: ["kb-1"],
      error_localizer_enabled: true,
    });

    expect(payload).toMatchObject({
      template_id: "template-1",
      name: "quality_check",
      model: "turing_large",
      mapping: { output: "answer" },
      error_localizer: true,
      filters: [],
      config: {
        run_config: {
          pass_threshold: 0.8,
          check_internet: true,
          knowledge_bases: ["kb-1"],
          error_localizer_enabled: true,
        },
      },
    });
    expect(payload).not.toHaveProperty("pass_threshold");
    expect(payload).not.toHaveProperty("check_internet");
    expect(payload).not.toHaveProperty("knowledge_bases");
  });

  it("drops mapping entries the user cleared (empty-string paths)", () => {
    // Auto-mapped fields the user removed come through as "" — sending them
    // makes the eval runner treat the key as a required attribute with an
    // empty path and fail every row ("Required attribute '' ... not found").
    // No mapping at all must serialize as {} so context-only evals run.
    const payload = serializeEvalConfig({
      templateId: "template-1",
      name: "context_only",
      mapping: { agent_prompt: "", conversation: "   ", output: "answer" },
    });

    expect(payload.mapping).toEqual({ output: "answer" });
  });

  it("serializes an all-cleared mapping as an empty object", () => {
    const payload = serializeEvalConfig({
      templateId: "template-1",
      name: "context_only",
      mapping: { agent_prompt: "", conversation: "" },
    });

    expect(payload.mapping).toEqual({});
  });

  it("keeps canonical filter lists unchanged", () => {
    const filters = [
      {
        column_id: "duration",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 10,
        },
      },
    ];

    expect(
      serializeEvalConfig({
        templateId: "template-1",
        name: "quality_check",
        filters,
      }).filters,
    ).toBe(filters);
  });
});

describe("getDecisionRunConfigOverrides", () => {
  it("merges only supported decision fields in template, runtime, editor order", () => {
    const model = "decision/openrouter/morph/reflex";
    const jev_mapping = { pass: { criteria_true: "Authored criterion" } };
    expect(
      getDecisionRunConfigOverrides({
        model,
        jev_mapping,
        pass_threshold: 0,
        config: {
          jev_mapping: { pass: { criteria_true: "Template criterion" } },
          rule_prompt: "Do not forward arbitrary template settings",
          eval_type_id: "CustomPromptEvaluator",
          run_config: {
            jev_mapping: { pass: { criteria_true: "Saved criterion" } },
            output_type: "Pass/Fail",
            pass_threshold: 0.9,
            internal_only: "excluded",
          },
        },
      }),
    ).toEqual({
      model,
      jev_mapping,
      pass_threshold: 0,
      output_type: "Pass/Fail",
    });
  });

  it("does not carry stale typed configuration into an ordinary model", () => {
    expect(
      getDecisionRunConfigOverrides({
        model: "ordinary-model",
        config: {
          run_config: { jev_mapping: { score: { levels: ["a", "b"] } } },
        },
      }),
    ).toEqual({});
  });
});

describe("sanitizeEvalMapping", () => {
  it("drops cleared fields and keeps real attribute paths", () => {
    expect(
      sanitizeEvalMapping({ a: "output.value", b: "", c: null, d: "   " }),
    ).toEqual({ a: "output.value" });
  });

  it("forwards a non-string value so the API rejects it by name", () => {
    // Dropping it here would silently delete the variable from the saved
    // config; the write gate answers it with a message instead.
    expect(sanitizeEvalMapping({ a: { value: "output.value" } })).toEqual({
      a: { value: "output.value" },
    });
  });
});
