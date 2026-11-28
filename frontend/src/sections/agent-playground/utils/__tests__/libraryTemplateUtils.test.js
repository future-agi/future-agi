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

  it.each(["text", "string", "json"])("accepts the %s response format with string output", (responseFormat) => {
    const config = toDetachedLibraryTemplateConfig({
      ...baseTemplate,
      prompt_config_snapshot: {
        ...baseTemplate.prompt_config_snapshot,
        configuration: {
          ...baseTemplate.prompt_config_snapshot.configuration,
          response_format: responseFormat,
          response_schema: responseSchema,
        },
      },
    });

    expect(config.outputFormat).toBe("string");
    expect(config.modelConfig.responseFormat).toBe(responseFormat === "string" ? "text" : responseFormat);
    expect(config.modelConfig.responseSchema).toBeNull();
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

  it.each([
    { output_format: "json" },
    { response_format: "json_object" },
    { response_format: "json_schema", response_schema: responseSchema },
    { model: "" },
    { model: "   " },
    { model: undefined },
  ])("rejects a template the existing drawer cannot save: %o", (configuration) => {
    expect(
      toDetachedLibraryTemplateConfig({
        ...baseTemplate,
        prompt_config_snapshot: {
          ...baseTemplate.prompt_config_snapshot,
          configuration: {
            ...baseTemplate.prompt_config_snapshot.configuration,
            ...configuration,
          },
        },
      }),
    ).toBeNull();
  });
});
