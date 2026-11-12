import { expect, it } from "vitest";

import { assertContractedRequestConfig } from "src/api/contracts/openapi-contract";

import { experimentBody, experimentBodyOfValues } from "../experimentBody";
import { getNewExperimentValidationSchema } from "../schema";
import { wireExperimentBody } from "../serverBody";

// Shape captured from the public prompt-prefilled wizard after selecting an eval.
const raw = {
  columnId: null,
  name: "Prompt experiment",
  description: "",
  outputFormat: "string",
  experimentType: "llm",
  promptConfig: [
    {
      _itemId: "prompt-card",
      experimentType: "llm",
      targetType: "prompt",
      promptId: "11111111-1111-4111-8111-111111111111",
      promptVersion: "22222222-2222-4222-8222-222222222222",
      agentId: null,
      agentVersion: null,
      model: [
        { id: "model-row", value: "gpt-4o", logoUrl: "", providers: "openai" },
      ],
      configuration: { toolChoice: "auto", tools: [] },
      modelParams: {
        "gpt-4o": {
          temperature: 0,
          maxTokens: 128,
          topP: null,
          presencePenalty: null,
          frequencyPenalty: null,
          responseFormat: "text",
        },
      },
      name: "Published prompt",
      outputFormat: "string",
      isDraft: false,
      variables: [],
      variableMapping: {},
      toolSetup: {
        mode: "mock",
        perTool: {},
        endpoints: {},
        onUnrecorded: "mock",
        versionTools: [],
        fixtures: [],
        recorded: { available: false, sessions: 0 },
        maxToolTurns: 8,
      },
      versionLabel: "v1",
      templateMessages: [{ role: "user", content: "Synthetic input" }],
    },
  ],
  userEvalMetrics: [
    {
      evalId: "33333333-3333-4333-8333-333333333333",
      evalTemplateName: "Output check",
      templateId: "33333333-3333-4333-8333-333333333333",
      mapping: { output: "output" },
      model: "gpt-4o",
      config: { mapping: { output: "output" } },
      templateType: "single",
      evalType: "llm",
      outputType: null,
      requiredKeys: [],
      name: "Output check",
    },
  ],
  datasetVersionId: null,
  splitIds: [],
  trials: 1,
  maxConcurrency: 10,
  metadata: [],
  rowIds: [],
};

it("submits the same supported settings that the live wizard preflight admits", async () => {
  const options = { datasetId: "44444444-4444-4444-8444-444444444444" };
  const preview = wireExperimentBody(
    experimentBodyOfValues(raw, options),
    {},
    false,
  );
  expect(preview.blocks).toEqual([]);
  const data = await getNewExperimentValidationSchema([]).parseAsync(raw);
  const submitted = wireExperimentBody(
    experimentBody(data, raw, options),
    {},
    false,
  );
  expect(submitted.blocks).toEqual([]);
  expect(submitted.body.prompt_config).toHaveLength(1);
  expect(submitted.body.user_eval_metrics).toHaveLength(1);
  expect(() =>
    assertContractedRequestConfig({
      method: "post",
      url: "/model-hub/experiments/v2/",
      data: submitted.body,
    }),
  ).not.toThrow();
});
