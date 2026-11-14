import { describe, expect, it } from "vitest";
import { normalizeConfigurationForSave } from "src/sections/workbench/createPrompt/common";
import { arrayToLabelValueMap } from "../common";
import { runPromptEventFields } from "../runPromptEventFields";

const PROMPT_TEXT = "SYNTHETIC PROMPT TEXT 41e7";
const SCHEMA_TEXT = "SYNTHETIC SCHEMA FIELD 0b9d";

// The model settings as RunPrompt's onSubmit stores them: the model's slider
// values in run_prompt_config, keyed by camel-cased label and then renamed to
// the API's snake_case names.
const runPromptConfig = (sliders) => ({
  model_type: "llm",
  modelName: "synthetic-model",
  ...normalizeConfigurationForSave(arrayToLabelValueMap(sliders)),
});

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
        // config.tools holds the selected tool ids (ToolsConfigSection).
        tool_choice: "auto",
        tools: ["tool-1"],
        response_format: "text",
        run_prompt_config: runPromptConfig([
          { label: "Max Tokens", value: 256 },
          { label: "Presence Penalty", value: 0 },
          { label: "Temperature", value: 0.2 },
          { label: "Top P", value: 1 },
        ]),
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

  it("reads the model settings the request keeps in run_prompt_config", () => {
    const fields = runPromptEventFields("dataset-3", {
      config: {
        model: "synthetic-model",
        response_format: "json_object",
        tool_choice: "auto",
        tools: ["tool-9"],
        run_prompt_config: runPromptConfig([
          { label: "Max Tokens", value: 512 },
          { label: "Presence Penalty", value: 0.5 },
          { label: "Top P", value: 0.9 },
          { label: "Temperature", value: 0.1 },
          { label: "Frequency Penalty", value: 0 },
        ]),
      },
    });

    expect(fields.model_options).toEqual({
      maxTokens: 512,
      presencePenalty: 0.5,
      frequencyPenalty: 0,
      temperature: 0.1,
      topP: 0.9,
      responseFormat: "json_object",
    });
    expect(fields.tools_config).toEqual({
      toolChoice: "auto",
      tools: ["tool-9"],
    });
  });

  it("reports the frequency penalty setting", () => {
    const fields = runPromptEventFields("dataset-6", {
      config: {
        run_prompt_config: runPromptConfig([
          { label: "Frequency Penalty", value: 0.7 },
        ]),
      },
    });

    expect(fields.model_options.frequencyPenalty).toBe(0.7);
  });

  it("sends a saved response schema's id and format, not the schema", () => {
    const fields = runPromptEventFields("dataset-4", {
      config: {
        model: "synthetic-model",
        // A saved schema chosen in the response format menu is sent as the
        // whole record.
        response_format: {
          id: "schema-1",
          name: "Synthetic schema",
          description: SCHEMA_TEXT,
          schema: { type: "object", properties: { [SCHEMA_TEXT]: {} } },
          schema_type: "json",
        },
      },
    });

    expect(fields.model_options.responseFormat).toBe("json_schema");
    expect(fields.model_options.responseSchemaId).toBe("schema-1");
    expect(JSON.stringify(fields)).not.toContain(SCHEMA_TEXT);
    expect(JSON.stringify(fields)).not.toContain("Synthetic schema");
  });

  it("sends only the type of an inline response format object", () => {
    const fields = runPromptEventFields("dataset-5", {
      config: {
        response_format: {
          type: "json_schema",
          json_schema: { name: SCHEMA_TEXT, schema: { type: "object" } },
        },
      },
    });

    expect(fields.model_options.responseFormat).toBe("json_schema");
    expect(fields.model_options).not.toHaveProperty("responseSchemaId");
    expect(JSON.stringify(fields)).not.toContain(SCHEMA_TEXT);
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
