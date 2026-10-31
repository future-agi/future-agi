import { describe, expect, it, vi } from "vitest";
import { getNewExperimentValidationSchema } from "../schema";
import { getVersionTemplateFormat } from "../utils";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { experiment: { validateName: "/validate-name/" } } },
}));

const baseForm = (experimentType, item) => ({
  name: "exp",
  experimentType,
  outputFormat: experimentType === "tts" ? "audio" : "string",
  promptConfig: [item],
  userEvalMetrics: [{ id: "eval-1", name: "eval" }],
});

const llmItem = (configuration) => ({
  experimentType: "llm",
  promptId: "prompt-1",
  promptVersion: "version-1",
  model: [{ id: "m1", value: "gpt-4o" }],
  modelParams: {},
  configuration,
  outputFormat: "string",
  unmappedVariables: 0,
});

const ttsItem = (configuration) => ({
  experimentType: "tts",
  messages: [
    { role: "system", content: [{ type: "text", text: "" }] },
    { role: "user", content: [{ type: "text", text: "Say hi" }] },
  ],
  model: [{ id: "m1", value: "tts-1", voices: [{ voice: "alloy" }] }],
  modelParams: {},
  configuration,
});

// isEditing=true skips the async name-uniqueness API check.
const parse = (form) =>
  getNewExperimentValidationSchema([], true, "dataset-1").parseAsync(form);

describe("experiment payload template_format", () => {
  it("keeps an explicit LLM template_format in the payload", async () => {
    const out = await parse(
      baseForm(
        "llm",
        llmItem({ toolChoice: "auto", tools: [], template_format: "jinja" }),
      ),
    );
    expect(out.promptConfig[0].configuration.template_format).toBe("jinja");
  });

  it("sends no LLM template_format when the user did not pick one", async () => {
    const out = await parse(
      baseForm("llm", llmItem({ toolChoice: "auto", tools: [] })),
    );
    expect(out.promptConfig[0].configuration).not.toHaveProperty(
      "template_format",
    );
  });

  it("keeps the TTS template_format in the payload", async () => {
    const out = await parse(
      baseForm(
        "tts",
        ttsItem({ toolChoice: "auto", tools: [], template_format: "jinja" }),
      ),
    );
    expect(out.promptConfig[0].configuration.template_format).toBe("jinja");
  });
});

describe("getVersionTemplateFormat", () => {
  it("reads the format from a dict snapshot", () => {
    expect(
      getVersionTemplateFormat({
        prompt_config_snapshot: { configuration: { template_format: "jinja" } },
      }),
    ).toBe("jinja");
  });

  it("reads the format from a list snapshot", () => {
    expect(
      getVersionTemplateFormat({
        prompt_config_snapshot: [
          { configuration: { template_format: "mustache" } },
        ],
      }),
    ).toBe("mustache");
  });

  it("returns null for versions saved without a format", () => {
    expect(
      getVersionTemplateFormat({ prompt_config_snapshot: { configuration: {} } }),
    ).toBeNull();
    expect(getVersionTemplateFormat(undefined)).toBeNull();
  });
});
