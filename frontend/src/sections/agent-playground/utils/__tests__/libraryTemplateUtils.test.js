import { describe, expect, it } from "vitest";
import { toDetachedLibraryTemplateConfig } from "../libraryTemplateUtils";
import {
  getLibraryTemplateItems,
  getNextLibraryTemplatePageParam,
} from "src/api/agent-playground/libraryTemplateResponseUtils";

const baseTemplate = {
  id: "template-1",
  name: "Research Template",
  prompt_config_snapshot: {
    messages: [{ role: "user", content: "Summarize {{topic}}" }],
    configuration: {
      model: "gpt-4o-mini",
      response_format: "text",
      output_format: "string",
      template_format: "jinja",
      tools: [{ name: "lookup" }],
      tool_choice: "required",
    },
  },
};

const responseSchema = {
  type: "object",
  properties: { answer: { type: "string" } },
  required: ["answer"],
};

describe("libraryTemplateUtils", () => {
  it("normalizes live and generated library-template list envelopes", () => {
    const liveItem = { id: "live" };
    const generatedItem = { id: "generated" };

    expect(getLibraryTemplateItems({ result: { data: [liveItem] } })).toEqual([
      liveItem,
    ]);
    expect(
      getLibraryTemplateItems({ result: { results: [generatedItem] } }),
    ).toEqual([generatedItem]);
    expect(getLibraryTemplateItems({ results: [generatedItem] })).toEqual([
      generatedItem,
    ]);
  });

  it("computes next page from generated next links and legacy counts", () => {
    expect(
      getNextLibraryTemplatePageParam({ data: { result: { next: "url" } } }, [
        { data: { result: { data: [] } } },
      ]),
    ).toBe(1);

    expect(
      getNextLibraryTemplatePageParam(
        { data: { result: { data: [{ id: "a" }], total_count: 3 } } },
        [
          { data: { result: { data: [{ id: "a" }] } } },
          { data: { result: { data: [{ id: "b" }] } } },
        ],
      ),
    ).toBe(2);
  });

  it("builds a detached prompt-node config and strips active tool settings", () => {
    const config = toDetachedLibraryTemplateConfig(baseTemplate);

    expect(config).toMatchObject({
      prompt_template_id: null,
      prompt_version_id: null,
      outputFormat: "string",
      templateFormat: "jinja",
      modelConfig: {
        model: "gpt-4o-mini",
        responseFormat: "text",
        tools: [],
        toolChoice: "auto",
      },
      payload: {
        promptConfig: [
          {
            configuration: expect.objectContaining({
              template_format: "jinja",
            }),
          },
        ],
      },
    });
  });

  it("preserves safe JSON schema settings for structured library imports", () => {
    const config = toDetachedLibraryTemplateConfig({
      ...baseTemplate,
      prompt_config_snapshot: {
        ...baseTemplate.prompt_config_snapshot,
        configuration: {
          response_format: "json_schema",
          response_schema: responseSchema,
          output_format: "json",
          template_format: "mustache",
        },
      },
    });

    expect(config).toMatchObject({
      outputFormat: "json",
      templateFormat: "mustache",
      modelConfig: {
        responseFormat: "json_schema",
        responseSchema,
      },
    });
  });

  it("drops response schemas for non-schema response formats", () => {
    ["text", "json_object", "string"].forEach((responseFormat) => {
      const config = toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            response_format: responseFormat,
            response_schema: responseSchema,
            output_format: "json",
          },
        },
      });

      expect(config.modelConfig.responseSchema).toBeNull();
      expect(config.payload.promptConfig[0].configuration).not.toHaveProperty(
        "response_schema",
      );
    });
  });

  it("rejects unsupported execution-affecting response and template settings", () => {
    expect(
      toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            ...baseTemplate.prompt_config_snapshot.configuration,
            template_format: "liquid",
          },
        },
      }),
    ).toBeNull();

    expect(
      toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            ...baseTemplate.prompt_config_snapshot.configuration,
            response_format: "custom-runtime-mode",
          },
        },
      }),
    ).toBeNull();
  });

  it("rejects response schemas with refs or excessive size before import", () => {
    expect(
      toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            response_format: "json_schema",
            response_schema: { $ref: "https://example.com/schema.json" },
            output_format: "json",
          },
        },
      }),
    ).toBeNull();

    expect(
      toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            response_format: "json_schema",
            response_schema: {
              type: "object",
              description: "x".repeat(50_001),
            },
            output_format: "json",
          },
        },
      }),
    ).toBeNull();

    expect(
      toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            response_format: "json_schema",
            response_schema: {
              type: "object",
              description: "é".repeat(25_000),
            },
            output_format: "json",
          },
        },
      }),
    ).toBeNull();
  });
});
