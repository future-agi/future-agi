import { describe, expect, it } from "vitest";
import {
  escapeModelKey,
  promptConfigTransform,
  reversePromptConfigTransform,
  slimModelParams,
} from "../utils";
import { paramsSummary } from "../StepperComponentExperiment/NewModelRenderWithParamsTool";

// options-truth L2 G8 (P1): model_params carry parameter values only.
describe("slimModelParams", () => {
  it("keeps scalar params and the known groups", () => {
    expect(
      slimModelParams({
        temperature: 0.9,
        maxTokens: 200,
        topP: 0.05,
        frequencyPenalty: 0.4,
        responseFormat: "json_object",
        booleans: { stream: false },
        dropdowns: { verbosity: "low" },
        reasoning: { sliders: { budget: 1024 }, showReasoningProcess: true },
      }),
    ).toEqual({
      temperature: 0.9,
      maxTokens: 200,
      topP: 0.05,
      frequencyPenalty: 0.4,
      responseFormat: "json_object",
      booleans: { stream: false },
      dropdowns: { verbosity: "low" },
      reasoning: { sliders: { budget: 1024 }, showReasoningProcess: true },
    });
  });

  it("drops an embedded form snapshot (the 1.9 MB recursion)", () => {
    const form = { promptConfig: [{ modelParams: {} }], userEvalMetrics: [] };
    form.promptConfig[0].modelParams.x = { ...form };
    const out = slimModelParams({ temperature: 0.4, ...form, config: form });
    expect(out).toEqual({ temperature: 0.4 });
    expect(JSON.stringify(out).length).toBeLessThan(50);
  });

  it("returns {} for nothing usable", () => {
    expect(slimModelParams(null)).toEqual({});
    expect(slimModelParams([1, 2])).toEqual({});
  });
});

describe("promptConfigTransform model params", () => {
  const card = (modelParams) => ({
    promptId: "p1",
    promptVersion: "v1",
    model: [
      { value: "gpt-4o-mini", providers: "openai" },
      { value: "gemini-2.5-flash", providers: "google" },
    ],
    modelParams,
  });

  it("sends each model's own params, dotted model keys stored escaped", () => {
    const out = promptConfigTransform(
      [
        card({
          "gpt-4o-mini": { temperature: 0.9, maxTokens: 200, topP: 0.05 },
          gemini__DOT__2__DOT__5_flash: { temperature: 0.1 },
          "gemini-2__DOT__5-flash": { temperature: 0.6, maxTokens: 50 },
        }),
      ],
      "llm",
    );
    expect(out).toHaveLength(2);
    expect(out[0].model_params).toMatchObject({
      temperature: 0.9,
      max_tokens: 200,
      top_p: 0.05,
    });
    expect(out[1].model_params).toMatchObject({
      temperature: 0.6,
      max_tokens: 50,
    });
  });

  it("keeps the POST small even if a snapshot leaked into the form", () => {
    const big = { promptConfig: Array.from({ length: 200 }, () => ({ a: 1 })) };
    const out = promptConfigTransform(
      [card({ "gpt-4o-mini": { temperature: 0.2, ...big } })],
      "llm",
    );
    expect(JSON.stringify(out).length).toBeLessThan(2000);
  });
});

describe("persisted dotted-model parameters", () => {
  it("escapes persisted keys for RHF and round-trips explicit values", () => {
    const model = "gpt-4.1";
    const params = { temperature: 0.9, max_tokens: 256 };
    const form = reversePromptConfigTransform(
      [
        {
          id: "run-a",
          promptId: "p1",
          promptVersion: "v1",
          model,
          modelParams: params,
        },
      ],
      "llm",
    );
    expect(form[0].modelParams).toEqual({ [escapeModelKey(model)]: params });
    expect(promptConfigTransform(form, "llm")[0].model_params).toMatchObject(
      params,
    );
  });
});

describe("paramsSummary", () => {
  it("shows the reasoning settings sent to the provider", () => {
    const reasoning = {
      dropdowns: { reasoningEffort: "low" },
      sliders: { budget: 1024 },
      showReasoningProcess: false,
    };
    expect(paramsSummary({ reasoning })).toBe(
      `reasoning ${JSON.stringify(reasoning)}`,
    );
  });
  it("shows every committed param that left its default", () => {
    expect(
      paramsSummary({ temperature: 0.9, maxTokens: 200, topP: 0.05 }),
    ).toBe("temp 0.9 · max 200 tokens · top_p 0.05");
    expect(
      paramsSummary({
        temperature: 0.3,
        maxTokens: 400,
        topP: 1,
        frequencyPenalty: 0.5,
        presencePenalty: 0,
        responseFormat: "json_object",
      }),
    ).toBe("temp 0.3 · max 400 tokens · freq_penalty 0.5 · format json_object");
    expect(paramsSummary({ responseFormat: "text" })).toBeNull();
  });
});
