import { describe, expect, it } from "vitest";
import { runPromptEventFields } from "../runPromptEventFields";

const PROMPT_TEXT = "SYNTHETIC PROMPT TEXT 41e7";

describe("runPromptEventFields", () => {
  it("sends message counts and lengths instead of the prompt text", () => {
    const fields = runPromptEventFields("dataset-1", {
      name: "Synthetic column",
      config: {
        concurrency: 4,
        model: "synthetic-model",
        messages: [
          { role: "system", content: PROMPT_TEXT },
          {
            role: "user",
            content: [
              { type: "text", text: `${PROMPT_TEXT} {{question}}` },
              { type: "image_url", image_url: { url: "https://x.test/a.png" } },
            ],
          },
        ],
        toolChoice: "auto",
        tools: [{ id: "tool-1", config: { endpoint: PROMPT_TEXT } }],
        maxTokens: 256,
        presencePenalty: 0,
        temperature: 0.2,
        topP: 1,
        responseFormat: "text",
      },
    });

    expect(fields).toEqual({
      id: "dataset-1",
      concurrency: 4,
      prompt_message_count: 2,
      prompt_length: PROMPT_TEXT.length * 2 + " {{question}}".length,
      tools_config: { toolChoice: "auto", tools: ["tool-1"] },
      model_name: "synthetic-model",
      model_options: {
        maxTokens: 256,
        presencePenalty: 0,
        temperature: 0.2,
        topP: 1,
        responseFormat: "text",
      },
    });
    expect(JSON.stringify(fields)).not.toContain(PROMPT_TEXT);
  });

  it("handles a run without messages or tools", () => {
    expect(runPromptEventFields("dataset-2", { config: {} })).toEqual({
      id: "dataset-2",
      concurrency: undefined,
      prompt_message_count: 0,
      prompt_length: 0,
      tools_config: { toolChoice: undefined, tools: [] },
      model_name: undefined,
      model_options: {
        maxTokens: undefined,
        presencePenalty: undefined,
        temperature: undefined,
        topP: undefined,
        responseFormat: undefined,
      },
    });
  });
});
