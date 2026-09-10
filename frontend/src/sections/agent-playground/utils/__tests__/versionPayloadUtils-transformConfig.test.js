import { describe, it, expect } from "vitest";
import { buildVersionPayload } from "../versionPayloadUtils";
import { NODE_TYPES } from "../constants";
import { createPromptNode } from "./fixtures";

const responseSchema = {
  type: "object",
  properties: {
    answer: { type: "string" },
  },
  required: ["answer"],
};

// ---------------------------------------------------------------------------
// Additional tests for buildPromptTemplateForApi (exercised through buildVersionPayload)
// The current implementation outputs prompt_template (not config) for atomic nodes.
// ---------------------------------------------------------------------------
describe("buildPromptTemplateForApi – via buildVersionPayload", () => {
  it("forwards numeric model parameters from configuration", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          modelConfig: { model: "gpt-4", modelDetail: {} },
          messages: [
            { role: "user", content: [{ type: "text", text: "Hello" }] },
          ],
          payload: {
            variable_names: {},
            promptConfig: [
              {
                configuration: {
                  temperature: 0.7,
                  max_tokens: 200,
                  top_p: 0.9,
                  frequency_penalty: 0.1,
                  presence_penalty: 0.2,
                },
              },
            ],
            ports: [],
          },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    expect(pt.temperature).toBe(0.7);
    expect(pt.max_tokens).toBe(200);
    expect(pt.top_p).toBe(0.9);
    expect(pt.frequency_penalty).toBe(0.1);
    expect(pt.presence_penalty).toBe(0.2);
  });

  it("forwards tools, responseFormat, toolChoice from configuration", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          modelConfig: { model: "gpt-4", modelDetail: {} },
          messages: [],
          payload: {
            promptConfig: [
              {
                configuration: {
                  tools: [{ name: "search" }],
                  responseFormat: "json_object",
                  toolChoice: "auto",
                },
              },
            ],
            ports: [],
          },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    expect(pt.tools).toEqual([{ name: "search" }]);
    expect(pt.response_format).toBe("json_object");
    expect(pt.tool_choice).toBe("auto");
  });

  it("preserves imported output, template format, and response schema in prompt_template", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          prompt_template_id: null,
          prompt_version_id: null,
          outputFormat: "json",
          templateFormat: "jinja",
          modelConfig: {
            model: "gpt-4o-mini",
            modelDetail: { model_name: "gpt-4o-mini", providers: "openai" },
            responseFormat: "json_schema",
            responseSchema,
            toolChoice: "required",
            tools: [{ name: "lookup" }],
          },
          messages: [
            { role: "user", content: [{ type: "text", text: "Hello" }] },
          ],
          payload: {
            promptConfig: [
              {
                configuration: {
                  maxTokens: 256,
                  topP: 0.8,
                  response_schema: responseSchema,
                  output_format: "json",
                  template_format: "jinja",
                },
              },
            ],
            ports: [],
          },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    expect(pt.prompt_template_id).toBeNull();
    expect(pt.prompt_version_id).toBeNull();
    expect(pt.response_format).toBe("json_schema");
    expect(pt.response_schema).toEqual(responseSchema);
    expect(pt.output_format).toBe("json");
    expect(pt.template_format).toBe("jinja");
    expect(pt.max_tokens).toBe(256);
    expect(pt.top_p).toBe(0.8);
    expect(pt.tool_choice).toBe("required");
    expect(pt.tools).toEqual([{ name: "lookup" }]);
  });

  it("preserves detached imported prompt config from _initialConfig during draft creation", () => {
    const nodes = [
      {
        id: "p1",
        type: NODE_TYPES.LLM_PROMPT,
        position: { x: 0, y: 0 },
        data: {
          label: "Imported Library Prompt",
          node_template_id: "tpl-prompt",
          config: {
            prompt_template_id: null,
            prompt_version_id: null,
          },
          _initialConfig: {
            prompt_template_id: null,
            prompt_version_id: null,
            outputFormat: "string",
            templateFormat: "jinja",
            modelConfig: {
              model: "gpt-4o-mini",
              modelDetail: {
                modelName: "gpt-4o-mini",
                providers: "openai",
              },
              responseFormat: "text",
              responseSchema: null,
              toolChoice: "auto",
              tools: [],
            },
            messages: [
              {
                role: "user",
                content: [{ type: "text", text: "Hello {{topic}}" }],
              },
            ],
            payload: {
              promptConfig: [
                {
                  configuration: {
                    output_format: "string",
                    template_format: "jinja",
                    temperature: 0.1,
                  },
                },
              ],
              ports: [],
            },
          },
        },
      },
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    expect(pt).toMatchObject({
      prompt_template_id: null,
      prompt_version_id: null,
      model: "gpt-4o-mini",
      output_format: "string",
      template_format: "jinja",
      temperature: 0.1,
      save_prompt_version: false,
    });
    expect(pt.messages).toEqual([
      {
        id: undefined,
        role: "user",
        content: [{ type: "text", text: "Hello {{topic}}" }],
      },
    ]);
  });

  it("serializes model-less imported templates with messages instead of dropping them", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          prompt_template_id: null,
          prompt_version_id: null,
          outputFormat: "string",
          templateFormat: "jinja",
          modelConfig: {
            model: "",
            modelDetail: {},
            responseFormat: "text",
            toolChoice: "auto",
            tools: [],
          },
          messages: [
            { role: "user", content: [{ type: "text", text: "Choose model" }] },
          ],
          payload: {
            promptConfig: [
              {
                configuration: {
                  template_format: "jinja",
                  output_format: "string",
                },
              },
            ],
            ports: [],
          },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    expect(pt).toMatchObject({
      prompt_template_id: null,
      prompt_version_id: null,
      model: null,
      model_detail: {},
      response_format: "text",
      output_format: "string",
      template_format: "jinja",
      save_prompt_version: false,
    });
    expect(pt.messages).toEqual([
      {
        id: undefined,
        role: "user",
        content: [{ type: "text", text: "Choose model" }],
      },
    ]);
  });

  it("includes variable_names indirectly via model and messages", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          modelConfig: { model: "gpt-4", modelDetail: {} },
          messages: [
            { role: "user", content: [{ type: "text", text: "{{name}}" }] },
          ],
          payload: {
            variable_names: { name: "John" },
            promptConfig: [],
            ports: [],
          },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    // The prompt_template contains model and messages
    expect(pt.model).toBe("gpt-4");
    expect(pt.messages[0].content).toEqual([
      { type: "text", text: "{{name}}" },
    ]);
  });

  it("returns null prompt_template when node has no config data", () => {
    // Build node manually to avoid createPromptNode defaults
    const nodes = [
      {
        id: "p1",
        type: "llm_prompt",
        position: { x: 0, y: 0 },
        data: { label: "Empty", config: {} },
      },
    ];

    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].prompt_template).toBeNull();
  });

  it("returns null prompt_template when model is missing", () => {
    const nodes = [
      {
        id: "p1",
        type: "llm_prompt",
        position: { x: 0, y: 0 },
        data: {
          label: "No model",
          config: {
            modelConfig: { model: null },
            messages: [],
          },
        },
      },
    ];

    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].prompt_template).toBeNull();
  });

  it("preserves content blocks as arrays in messages", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          modelConfig: { model: "gpt-4", modelDetail: {} },
          messages: [
            {
              role: "user",
              content: [
                { type: "text", text: "Hello " },
                { type: "text", text: "world" },
              ],
            },
          ],
          payload: { ports: [] },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    expect(result.nodes[0].prompt_template.messages[0].content).toEqual([
      { type: "text", text: "Hello " },
      { type: "text", text: "world" },
    ]);
  });

  it("handles string content in messages", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          modelConfig: { model: "gpt-4", modelDetail: {} },
          messages: [{ role: "user", content: "plain string" }],
          payload: { ports: [] },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    // String content gets wrapped in block array
    expect(result.nodes[0].prompt_template.messages[0].content).toEqual([
      { type: "text", text: "plain string" },
    ]);
  });

  it("uses null for undefined numeric params", () => {
    const nodes = [
      createPromptNode("p1", {
        config: {
          modelConfig: { model: "gpt-4", modelDetail: {} },
          messages: [],
          payload: {
            promptConfig: [
              {
                configuration: {
                  temperature: 0.5,
                  // max_tokens not set
                },
              },
            ],
            ports: [],
          },
        },
      }),
    ];

    const result = buildVersionPayload(nodes, []);
    const pt = result.nodes[0].prompt_template;

    expect(pt.temperature).toBe(0.5);
    // Unset params default to null
    expect(pt.max_tokens).toBeNull();
  });
});
