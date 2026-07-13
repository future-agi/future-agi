import { describe, it, expect } from "vitest";
import { mapVersionToFormConfig } from "../promptVersionUtils";

// A version object as returned by /model-hub/prompt-history-executions/ —
// snake_case wire shape (the axios layer does not camelize responses).
const apiVersion = {
  id: "5673c644-e46d-443b-a346-64976b6387ad",
  template_version: "v3",
  is_draft: false,
  prompt_config_snapshot: {
    messages: [
      { role: "system", content: [{ type: "text", text: "be terse" }] },
      { role: "user", content: [{ type: "text", text: "hi" }] },
    ],
    placeholders: [],
    configuration: {
      model: "gpt-4o",
      model_detail: { model_name: "gpt-4o", providers: "openai" },
      tools: [{ name: "search" }],
      tool_choice: "auto",
      temperature: 0.7,
      max_tokens: 512,
      top_p: 0.9,
      frequency_penalty: 0.3,
      presence_penalty: 0.1,
      response_format: "text",
      output_format: "json",
    },
  },
};

describe("mapVersionToFormConfig", () => {
  it("reads snake_case configuration fields from prompt_config_snapshot", () => {
    const cfg = mapVersionToFormConfig(apiVersion);

    expect(cfg.modelConfig.model).toBe("gpt-4o");
    expect(cfg.modelConfig.modelDetail).toEqual({
      model_name: "gpt-4o",
      providers: "openai",
    });
    expect(cfg.modelConfig.toolChoice).toBe("auto");
    expect(cfg.modelConfig.tools).toEqual([{ name: "search" }]);
    expect(cfg.modelConfig.responseFormat).toBe("text");
    expect(cfg.outputFormat).toBe("json");
  });

  it("maps snake_case penalty/token params into the payload config", () => {
    const { payload } = mapVersionToFormConfig(apiVersion);
    const config = payload.promptConfig[0].configuration;

    expect(config.temperature).toBe(0.7);
    expect(config.maxTokens).toBe(512);
    expect(config.topP).toBe(0.9);
    expect(config.frequencyPenalty).toBe(0.3);
    expect(config.presencePenalty).toBe(0.1);
  });

  it("preserves snapshot messages by role and content", () => {
    const { messages } = mapVersionToFormConfig(apiVersion);

    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({
      role: "system",
      content: [{ type: "text", text: "be terse" }],
    });
    expect(messages[1]).toMatchObject({
      role: "user",
      content: [{ type: "text", text: "hi" }],
    });
  });

  it("does not silently return empty config for a snake_case payload (TH-6294)", () => {
    // The bug read camelCase keys (promptConfigSnapshot/modelDetail/...), so the
    // whole mapping came back empty. Guard that real fields now flow through.
    const cfg = mapVersionToFormConfig(apiVersion);
    expect(cfg.modelConfig.model).not.toBe("");
    expect(cfg.modelConfig.modelDetail).not.toEqual({});
  });

  it("falls back to defaults when the snapshot is missing", () => {
    const cfg = mapVersionToFormConfig({ id: "x", template_version: "v1" });

    expect(cfg.modelConfig.model).toBe("");
    expect(cfg.modelConfig.modelDetail).toEqual({});
    expect(cfg.outputFormat).toBe("string");
    // A system and a user message are always injected.
    expect(cfg.messages.map((m) => m.role)).toEqual(["system", "user"]);
  });
});
