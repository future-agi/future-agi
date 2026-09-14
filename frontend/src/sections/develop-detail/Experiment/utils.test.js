import { describe, expect, it } from "vitest";
import { promptConfigTransform } from "./utils";

const promptConfig = (configuration = {}) => ({
  experimentType: "llm",
  promptId: "prompt-id",
  promptVersion: "version-id",
  model: [{ value: "gpt-4o" }],
  modelParams: {},
  configuration: {
    toolChoice: "auto",
    tools: [],
    ...configuration,
  },
});

describe("experiment prompt template format", () => {
  it("carries the selected prompt version format into the API payload", () => {
    const [result] = promptConfigTransform(
      [promptConfig({ template_format: "jinja" })],
      "llm",
      "string",
    );

    expect(result.configuration.template_format).toBe("jinja");
  });

  it("defaults an omitted format to Mustache", () => {
    const [result] = promptConfigTransform(
      [promptConfig()],
      "llm",
      "string",
    );

    expect(result.configuration.template_format).toBe("mustache");
  });
});
