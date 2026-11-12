// L7b G8: `Open in Prompts with this row ↗` hands the workbench the row's
// variables and the run's model and params; the workbench applies them
// over the draft's values for what the state names.
import { describe, expect, it } from "vitest";
import { promptLinkOf } from "src/sections/common/RunSettings/targetLinks";
import { promptPrefillState } from "src/sections/experiment-detail/results/drawer/OutputCard";
import {
  prefillOf,
  promptDoorState,
  withPrefilledModel,
  withPrefilledVariables,
} from "../workbenchPrefill";

const run2B = {
  run_id: "r-2b",
  target_type: "prompt",
  target: {
    prompt_id: "p1",
    prompt_version_name: "V4",
    model: "claude-sonnet-4-5",
    model_params: { temperature: 0.3, max_tokens: 400 },
  },
};
const row41 = {
  inputs: { customer_message: "Please cancel A-3972", customer_tier: "free" },
  metadata: { language: "en", rank: 3 },
};

describe("promptDoorState / promptPrefillState", () => {
  it("carries the row's variables and the run's model and params", () => {
    expect(promptPrefillState(row41, run2B)).toEqual({
      prefillVariables: {
        language: "en",
        customer_message: "Please cancel A-3972",
        customer_tier: "free",
      },
      prefillModel: "claude-sonnet-4-5",
      prefillParams: { temperature: 0.3, max_tokens: 400 },
    });
  });

  it("the run's R1 config wins over its target", () => {
    const config = {
      model: { id: "gpt-4o-mini", params: { temperature: 0.7 } },
    };
    const state = promptPrefillState(row41, run2B, config);
    expect(state.prefillModel).toBe("gpt-4o-mini");
    expect(state.prefillParams).toEqual({ temperature: 0.7 });
  });

  it("maps variables through the run's variable_mapping", () => {
    const config = { variable_mapping: { question: "customer_message" } };
    expect(
      promptPrefillState(row41, run2B, config).prefillVariables.question,
    ).toBe("Please cancel A-3972");
  });

  it("a run-level door carries no variables", () => {
    const state = promptDoorState(run2B);
    expect(state.prefillVariables).toBeUndefined();
    expect(state.prefillModel).toBe("claude-sonnet-4-5");
  });

  it("goes with the one prompt link (the run's version)", () => {
    expect(promptLinkOf(run2B.target)).toBe(
      "/dashboard/workbench/create/p1?tab=Playground&version=V4",
    );
  });
});

describe("prefillOf", () => {
  it("reads the three fields, or null", () => {
    expect(prefillOf(undefined)).toBeNull();
    expect(prefillOf({ other: 1 })).toBeNull();
    expect(prefillOf({ prefillModel: "m" })).toEqual({
      variables: null,
      model: "m",
      params: null,
    });
    expect(prefillOf(promptPrefillState(row41, run2B))).toEqual({
      variables: promptPrefillState(row41, run2B).prefillVariables,
      model: "claude-sonnet-4-5",
      params: { temperature: 0.3, max_tokens: 400 },
    });
  });
});

describe("withPrefilledVariables", () => {
  it("overrides the draft's values only for the prompt's variables", () => {
    const draft = {
      customer_message: ["I was charged twice for order A-4411"],
      customer_tier: ["pro"],
    };
    expect(
      withPrefilledVariables(draft, {
        customer_message: "Please cancel A-3972",
        customer_tier: "free",
        language: "en",
      }),
    ).toEqual({
      customer_message: ["Please cancel A-3972"],
      customer_tier: ["free"],
    });
  });
  it("keeps the draft when nothing is prefilled", () => {
    const draft = { a: ["x"] };
    expect(withPrefilledVariables(draft, null)).toEqual(draft);
  });
});

describe("withPrefilledModel", () => {
  const v4 = {
    id: "c1",
    model: "gpt-4o-mini",
    model_detail: {
      model_name: "gpt-4o-mini",
      logo_url: "openai.png",
      providers: "openai",
      type: "chat",
    },
    temperature: 0.2,
    maxTokens: 600,
  };
  it("sets the run's model and its params", () => {
    const next = withPrefilledModel(v4, {
      model: "claude-sonnet-4-5",
      params: { temperature: 0.3, max_tokens: 400 },
    });
    expect(next.model).toBe("claude-sonnet-4-5");
    expect(next.model_detail.model_name).toBe("claude-sonnet-4-5");
    expect(next.model_detail.type).toBe("chat");
    expect(next.model_detail.logo_url).toBe("");
    expect(next.temperature).toBe(0.3);
    expect(next.maxTokens).toBe(400);
    expect(next.max_tokens).toBeUndefined();
    expect(next.id).toBe("c1");
  });
  it("the same model keeps its detail", () => {
    const next = withPrefilledModel(v4, { model: "gpt-4o-mini", params: null });
    expect(next.model_detail).toEqual(v4.model_detail);
    expect(next.temperature).toBe(0.2);
  });
});
