import { describe, expect, it, vi } from "vitest";
import { NODE_TYPES } from "../../../../utils/constants";
import { getDefaultValues, mapNodeDetailToNodeData } from "../../nodeFormUtils";

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

describe("mapNodeDetailToNodeData", () => {
  it("uses snake_case payload output_format when hydrating prompt defaults", () => {
    const defaults = getDefaultValues({
      type: NODE_TYPES.LLM_PROMPT,
      data: {
        label: "json_prompt",
        config: {
          payload: {
            promptConfig: [
              {
                configuration: {
                  output_format: "json",
                },
              },
            ],
          },
        },
      },
    });

    expect(defaults.outputFormat).toBe("json");
  });

  it("preserves prompt payload defaults for imported model parameters", () => {
    const payload = {
      promptConfig: [
        {
          configuration: {
            temperature: 0.2,
            maxTokens: 512,
            topP: 0.9,
          },
        },
      ],
    };

    const defaults = getDefaultValues({
      type: NODE_TYPES.LLM_PROMPT,
      data: {
        label: "imported_prompt",
        config: {
          outputFormat: "json",
          payload,
        },
      },
    });

    expect(defaults.payload).toBe(payload);
  });

  it("maps snake_case prompt_template node detail into prompt form store data", () => {
    const mapped = mapNodeDetailToNodeData(
      {
        name: "api_prompt",
        prompt_template: {
          prompt_template_id: "prompt-template-id",
          prompt_version_id: "prompt-version-id",
          output_format: "string",
          template_format: "jinja",
          model: "gpt-4o-mini",
          model_detail: { modelName: "GPT-4o mini" },
          response_format: {
            id: "schema-id",
            name: "Schema",
            schema: { type: "object" },
          },
          tool_choice: "required",
          tools: [{ type: "function", function: { name: "lookup" } }],
          temperature: 0.2,
          max_tokens: 256,
          top_p: 0.9,
          frequency_penalty: 0.1,
          presence_penalty: 0.3,
          messages: [
            {
              role: "user",
              content: "Write one test fact about {{topic}}.",
            },
          ],
        },
        ports: [
          {
            id: "port-output",
            key: "response",
            display_name: "response",
            direction: "output",
            data_schema: { type: "string" },
            required: false,
          },
          {
            id: "port-input",
            key: "topic",
            display_name: "topic",
            direction: "input",
            data_schema: { type: "string" },
            required: true,
          },
        ],
      },
      {
        id: "node-id",
        type: NODE_TYPES.LLM_PROMPT,
        data: {
          label: "old_prompt",
          config: {
            modelConfig: {
              tools: [],
            },
          },
        },
      },
    );

    expect(mapped.data.label).toBe("api_prompt");
    expect(mapped.data.ports).toEqual([
      {
        id: "port-output",
        key: "response",
        display_name: "response",
        direction: "output",
        data_schema: { type: "string" },
        required: false,
      },
    ]);
    expect(mapped.data.config).toMatchObject({
      prompt_template_id: "prompt-template-id",
      prompt_version_id: "prompt-version-id",
      outputFormat: "string",
      templateFormat: "jinja",
      modelConfig: {
        model: "gpt-4o-mini",
        modelDetail: { modelName: "GPT-4o mini" },
        responseFormat: "schema-id",
        responseSchema: {
          id: "schema-id",
          name: "Schema",
          schema: { type: "object" },
        },
        toolChoice: "required",
        tools: [{ type: "function", function: { name: "lookup" } }],
      },
    });
    expect(mapped.data.config.messages).toEqual([
      {
        id: "random-id",
        role: "system",
        content: [{ type: "text", text: "" }],
      },
      {
        id: "msg-0",
        role: "user",
        content: [
          {
            type: "text",
            text: "Write one test fact about {{topic}}.",
          },
        ],
      },
    ]);
    expect(mapped.data.config.payload.promptConfig[0].configuration).toEqual({
      temperature: 0.2,
      maxTokens: 256,
      topP: 0.9,
      frequencyPenalty: 0.1,
      presencePenalty: 0.3,
      tools: [{ type: "function", function: { name: "lookup" } }],
      toolChoice: "required",
      template_format: "jinja",
    });
  });

  it("maps separate response_schema from prompt_template node detail", () => {
    const mapped = mapNodeDetailToNodeData(
      {
        name: "api_prompt",
        prompt_template: {
          prompt_template_id: "prompt-template-id",
          prompt_version_id: "prompt-version-id",
          output_format: "json",
          template_format: "jinja",
          model: "gpt-4o-mini",
          model_detail: { modelName: "GPT-4o mini" },
          response_format: "json_schema",
          response_schema: responseSchema,
          messages: [
            {
              role: "user",
              content: [{ type: "text", text: "Return an answer." }],
            },
          ],
        },
        ports: [],
      },
      {
        id: "node-id",
        type: NODE_TYPES.LLM_PROMPT,
        data: { label: "old_prompt", config: {} },
      },
    );

    expect(mapped.data.config.modelConfig).toMatchObject({
      responseFormat: "json_schema",
      responseSchema,
    });
    expect(
      mapped.data.config.payload.promptConfig[0].configuration.response_schema,
    ).toEqual(responseSchema);
  });

  it("maps snake_case agent node detail fields into agent form store data", () => {
    const inputMappings = [
      {
        source_node_id: "source-node",
        source_port_id: "source-port",
        target_port_id: "target-port",
      },
    ];
    const mapped = mapNodeDetailToNodeData(
      {
        name: "nested_agent",
        ref_graph_id: "graph-id",
        ref_graph_version_id: "version-id",
        input_mappings: inputMappings,
        ports: [],
      },
      {
        id: "node-id",
        type: NODE_TYPES.AGENT,
        data: {
          label: "old_agent",
          config: {
            payload: {
              inputMappings: [],
            },
          },
        },
      },
    );

    expect(mapped.data.label).toBe("nested_agent");
    expect(mapped.data.graphId).toBe("graph-id");
    expect(mapped.data.versionId).toBe("version-id");
    expect(mapped.data.ref_graph_version_id).toBe("version-id");
    expect(mapped.data.config).toMatchObject({
      graphId: "graph-id",
      versionId: "version-id",
      payload: {
        inputMappings,
      },
    });
  });
});
