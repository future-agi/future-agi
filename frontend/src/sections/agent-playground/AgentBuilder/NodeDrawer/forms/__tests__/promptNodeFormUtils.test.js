import { describe, it, expect, vi } from "vitest";
import {
  extractVariablesFromContent,
  buildPromptNodePayload,
  buildPatchPayload,
  mapPatchResponseToStoreData,
} from "../promptNodeFormUtils";

// Mock getRandomId to return deterministic IDs
vi.mock("src/utils/utils", () => ({
  getRandomId: vi.fn(() => "random-id"),
}));

const responseSchema = {
  type: "object",
  properties: {
    answer: { type: "string" },
  },
  required: ["answer"],
};

// ---------------------------------------------------------------------------
// extractVariablesFromContent
// ---------------------------------------------------------------------------
describe("extractVariablesFromContent", () => {
  it("extracts single variable from text block", () => {
    const content = [{ type: "text", text: "Hello {{name}}" }];
    expect(extractVariablesFromContent(content)).toEqual(["name"]);
  });

  it("extracts multiple variables", () => {
    const content = [
      { type: "text", text: "{{greeting}} {{name}}, welcome to {{place}}" },
    ];
    const result = extractVariablesFromContent(content);
    expect(result).toContain("greeting");
    expect(result).toContain("name");
    expect(result).toContain("place");
    expect(result).toHaveLength(3);
  });

  it("deduplicates variables", () => {
    const content = [{ type: "text", text: "{{name}} and {{name}} again" }];
    expect(extractVariablesFromContent(content)).toEqual(["name"]);
  });

  it("handles whitespace inside braces", () => {
    const content = [{ type: "text", text: "{{  name  }}" }];
    expect(extractVariablesFromContent(content)).toEqual(["name"]);
  });

  it("ignores non-text blocks", () => {
    const content = [
      { type: "image_url", image_url: { url: "{{not_a_var}}" } },
      { type: "text", text: "{{real_var}}" },
    ];
    expect(extractVariablesFromContent(content)).toEqual(["real_var"]);
  });

  it("returns empty array for null/undefined input", () => {
    expect(extractVariablesFromContent(null)).toEqual([]);
    expect(extractVariablesFromContent(undefined)).toEqual([]);
  });

  it("returns empty array for empty content", () => {
    expect(extractVariablesFromContent([])).toEqual([]);
  });

  it("returns empty array for text without variables", () => {
    const content = [{ type: "text", text: "Hello world" }];
    expect(extractVariablesFromContent(content)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// buildPromptNodePayload
// ---------------------------------------------------------------------------
describe("buildPromptNodePayload", () => {
  const baseFormData = {
    name: "Test Prompt",
    modelConfig: {
      model: "gpt-4",
      modelDetail: { modelName: "GPT-4" },
      responseFormat: "text",
      toolChoice: "auto",
      tools: [],
    },
    messages: [
      {
        id: "msg-0",
        role: "user",
        content: [{ type: "text", text: "Hello {{name}}" }],
      },
    ],
  };

  it("builds complete payload with all fields", () => {
    const payload = buildPromptNodePayload(baseFormData, null);
    expect(payload.name).toBe("Test Prompt");
    expect(payload.promptConfig).toHaveLength(1);
    expect(payload.promptConfig[0].messages).toHaveLength(1);
    expect(payload.isRun).toBe(true);
    expect(payload.evaluationConfigs).toEqual([]);
  });

  it("does not include ports in payload (BE auto-creates them)", () => {
    const payload = buildPromptNodePayload(baseFormData, null);
    expect(payload.ports).toBeUndefined();
  });

  it("includes configuration with model info", () => {
    const payload = buildPromptNodePayload(baseFormData, null);
    const config = payload.promptConfig[0].configuration;
    expect(config.model).toBe("gpt-4");
    expect(config.modelDetail).toEqual({ modelName: "GPT-4" });
    expect(config.responseFormat).toBe("text");
    expect(config.toolChoice).toBe("auto");
  });

  it("preserves the selected output format in the form payload", () => {
    const payload = buildPromptNodePayload(
      { ...baseFormData, outputFormat: "json" },
      null,
    );

    expect(payload.promptConfig[0].configuration.outputFormat).toBe("json");
  });

  it("includes separate response schema for schema-backed output formats", () => {
    const payload = buildPromptNodePayload(
      {
        ...baseFormData,
        modelConfig: {
          ...baseFormData.modelConfig,
          responseFormat: "json_schema",
          responseSchema,
        },
      },
      null,
    );
    const config = payload.promptConfig[0].configuration;

    expect(config.responseFormat).toBe("json_schema");
    expect(config.responseSchema).toEqual(responseSchema);
    expect(config.response_schema).toEqual(responseSchema);
  });

  it("flattens known slider keys from model parameters by id", () => {
    const modelParameters = {
      sliders: [
        { id: "temperature", label: "Temperature", value: 0.7 },
        { id: "maxTokens", label: "Max Tokens", value: 100 },
      ],
    };
    const payload = buildPromptNodePayload(baseFormData, modelParameters);
    const config = payload.promptConfig[0].configuration;
    expect(config.temperature).toBe(0.7);
    expect(config.maxTokens).toBe(100);
  });

  it("sets null for known slider keys not present in parameters", () => {
    const modelParameters = {
      sliders: [{ id: "temperature", label: "Temperature", value: 0.7 }],
    };
    const payload = buildPromptNodePayload(baseFormData, modelParameters);
    const config = payload.promptConfig[0].configuration;
    expect(config.temperature).toBe(0.7);
    expect(config.maxTokens).toBeNull();
    expect(config.topP).toBeNull();
  });

  it("includes reasoning as nested object from model parameters", () => {
    const modelParameters = {
      sliders: [],
      reasoning: {
        sliders: [
          { id: "reasoning_effort", label: "Reasoning Effort", value: 0.8 },
        ],
        dropdowns: [
          { id: "reasoning_mode", label: "Reasoning Mode", value: "advanced" },
        ],
        showReasoningProcess: true,
      },
    };
    const payload = buildPromptNodePayload(baseFormData, modelParameters);
    const config = payload.promptConfig[0].configuration;
    expect(config.reasoning.sliders.reasoning_effort).toBe(0.8);
    expect(config.reasoning.dropdowns.reasoning_mode).toBe("advanced");
    expect(config.reasoning.showReasoningProcess).toBe(true);
  });

  it("defaults reasoning when not provided in model parameters", () => {
    const modelParameters = {
      sliders: [],
    };
    const payload = buildPromptNodePayload(baseFormData, modelParameters);
    const config = payload.promptConfig[0].configuration;
    expect(config.reasoning).toEqual({
      sliders: {},
      dropdowns: {},
      showReasoningProcess: true,
    });
  });

  it("handles empty messages", () => {
    const formData = { ...baseFormData, messages: [] };
    const payload = buildPromptNodePayload(formData, null);
    expect(payload.promptConfig[0].messages).toEqual([]);
  });

  it("handles null modelParameters", () => {
    const payload = buildPromptNodePayload(baseFormData, null);
    expect(payload.promptConfig[0].configuration).toBeDefined();
    expect(payload.promptConfig[0].configuration.model).toBe("gpt-4");
  });
});

// ---------------------------------------------------------------------------
// buildPatchPayload
// ---------------------------------------------------------------------------
describe("buildPatchPayload", () => {
  it("maps camelCase prompt payload config into snake_case PATCH contract fields", () => {
    const formData = {
      name: "saved_prompt",
      version: "v1",
      templateFormat: "jinja",
      modelConfig: {
        model: "gpt-4o-mini",
        modelDetail: { modelName: "GPT-4o mini" },
        responseFormat: "json",
        toolChoice: "required",
        tools: [{ type: "function", function: { name: "lookup" } }],
      },
      messages: [
        {
          id: "msg-0",
          role: "user",
          content: [{ type: "text", text: "Hello {{topic}}" }],
        },
      ],
    };
    const modelParameters = {
      sliders: [
        { id: "temperature", value: 0.3 },
        { id: "maxTokens", value: 512 },
        { id: "topP", value: 0.95 },
        { id: "presencePenalty", value: 0.2 },
        { id: "frequencyPenalty", value: 0.1 },
      ],
    };
    const payload = buildPromptNodePayload(formData, modelParameters);

    const patch = buildPatchPayload(
      {
        label: formData.name,
        config: {
          modelConfig: formData.modelConfig,
          messages: formData.messages,
          templateFormat: formData.templateFormat,
          payload,
        },
      },
      {
        prompt_template_id: "prompt-template-id",
        prompt_version_id: "prompt-version-id",
      },
    );

    expect(patch.name).toBe("saved_prompt");
    expect(patch.prompt_template).toMatchObject({
      prompt_template_id: "prompt-template-id",
      prompt_version_id: "prompt-version-id",
      model: "gpt-4o-mini",
      response_format: "json",
      output_format: "string",
      temperature: 0.3,
      max_tokens: 512,
      top_p: 0.95,
      presence_penalty: 0.2,
      frequency_penalty: 0.1,
      tool_choice: "required",
      template_format: "jinja",
      save_prompt_version: false,
    });
    expect(patch.prompt_template.tools).toEqual(formData.modelConfig.tools);
    expect(patch.prompt_template.messages).toEqual(formData.messages);
  });

  it("uses config outputFormat when payload configuration omits output_format", () => {
    const patch = buildPatchPayload(
      {
        label: "imported_prompt",
        config: {
          outputFormat: "json",
          templateFormat: "jinja",
          modelConfig: {
            model: "gpt-4o-mini",
            responseFormat: "json",
          },
          messages: [
            {
              id: "msg-0",
              role: "user",
              content: [{ type: "text", text: "Return JSON for {{topic}}" }],
            },
          ],
          payload: {
            promptConfig: [
              {
                configuration: {
                  template_format: "jinja",
                },
              },
            ],
          },
        },
      },
      {
        prompt_template_id: null,
        prompt_version_id: null,
      },
    );

    expect(patch.prompt_template.output_format).toBe("json");
    expect(patch.prompt_template.template_format).toBe("jinja");
  });

  it("preserves separate response_schema for schema-backed PATCH payloads", () => {
    const patch = buildPatchPayload(
      {
        label: "schema_prompt",
        config: {
          outputFormat: "json",
          templateFormat: "jinja",
          modelConfig: {
            model: "gpt-4o-mini",
            responseFormat: "json_schema",
            responseSchema,
          },
          messages: [
            {
              id: "msg-0",
              role: "user",
              content: [{ type: "text", text: "Return an answer." }],
            },
          ],
          payload: {
            promptConfig: [
              {
                configuration: {
                  output_format: "json",
                  template_format: "jinja",
                  response_schema: responseSchema,
                },
              },
            ],
          },
        },
      },
      {
        prompt_template_id: null,
        prompt_version_id: null,
      },
    );

    expect(patch.prompt_template.response_format).toBe("json_schema");
    expect(patch.prompt_template.response_schema).toEqual(responseSchema);
    expect(patch.prompt_template.output_format).toBe("json");
    expect(patch.prompt_template.template_format).toBe("jinja");
  });
});

describe("mapPatchResponseToStoreData", () => {
  it("maps separate response_schema from PATCH responses into form config", () => {
    const mapped = mapPatchResponseToStoreData({
      name: "schema_prompt",
      ports: [
        {
          id: "port-response",
          key: "response",
          display_name: "response",
          direction: "output",
          data_schema: { type: "string" },
          required: true,
        },
      ],
      prompt_template: {
        prompt_template_id: "prompt-template-id",
        prompt_version_id: "prompt-version-id",
        model: "gpt-4o-mini",
        model_detail: { modelName: "GPT-4o mini" },
        response_format: "json_schema",
        response_schema: responseSchema,
        output_format: "json",
        template_format: "jinja",
        tool_choice: "auto",
        tools: [],
        messages: [
          {
            role: "user",
            content: [{ type: "text", text: "Return an answer." }],
          },
        ],
      },
    });

    expect(mapped.config.modelConfig).toMatchObject({
      responseFormat: "json_schema",
      responseSchema,
    });
    expect(mapped.config.outputFormat).toBe("json");
    expect(
      mapped.config.payload.promptConfig[0].configuration.output_format,
    ).toBe("json");
    expect(
      mapped.config.payload.promptConfig[0].configuration.response_schema,
    ).toEqual(responseSchema);
    expect(mapped.ports[0].data_schema).toEqual(responseSchema);
  });
});
