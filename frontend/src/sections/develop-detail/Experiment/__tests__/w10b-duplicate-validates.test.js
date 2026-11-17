// Duplicate & edit, then Run experiment, sent nothing. The
// copy's cards come from the saved configurations (cardsFromSavedConfigs);
// a graph version's name is its number (ExperimentAgentConfigOutputSerializer
// agent_version_name = graph_version.version_number), and the form's check
// (schema.js versionLabel: z.string()) refused it, so Run only raised a toast.
import { describe, expect, it, vi } from "vitest";
import { cardsFromExperiment } from "../StartFromExperiment";
import { getNewExperimentValidationSchema } from "../schema";
import { experimentBody } from "../experimentBody";
import { wireExperimentBody } from "../serverBody";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/develop/r2", () => ({ useRunConfigs: vi.fn() }));

const DATASET = "99999999-9999-4999-8999-999999999999";
const prompt = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Answer a question-liquid/lfm-3-1.7b",
  prompt_id: "22222222-2222-4222-8222-222222222222",
  prompt_name: "Answer a question",
  prompt_version: "33333333-3333-4333-8333-333333333333",
  prompt_version_name: "v1",
  model: "liquid/lfm-3-1.7b",
  model_display_name: "liquid/lfm-3-1.7b",
  model_config: {},
  model_params: { providers: "custom", logoUrl: "", temperature: 0.2 },
  configuration: { toolChoice: "auto", tools: [] },
  output_format: "string",
  messages: [],
  voice_input_column_id: null,
  order: 0,
};
const agent = {
  id: "55555555-5555-4555-8555-555555555555",
  name: "Answer graph",
  agent_id: "66666666-6666-4666-8666-666666666666",
  agent_name: "Answer graph",
  agent_version: "77777777-7777-4777-8777-777777777777",
  agent_version_name: 2,
  order: 1,
};
const detail = {
  id: "88888888-8888-4888-8888-888888888888",
  name: "Model flows",
  dataset_id: DATASET,
  column_id: null,
  experiment_type: "llm",
  status: "completed",
  prompt_configs: [prompt],
  agent_configs: [agent],
  user_eval_metrics: [],
};
// The evaluation added on the Evaluations step before Review & run.
const addedEval = {
  evalId: "44444444-4444-4444-8444-444444444444",
  evalTemplateName: "Output check",
  templateId: "44444444-4444-4444-8444-444444444444",
  mapping: { output: "output" },
  model: "gpt-4o",
  config: { mapping: { output: "output" } },
  templateType: "single",
  evalType: "llm",
  outputType: null,
  requiredKeys: [],
  name: "Output check",
};
const duplicate = () => ({
  ...cardsFromExperiment(detail, {}, { mode: "duplicate" }),
  userEvalMetrics: [addedEval],
});

describe("A Duplicate & edit copy passes the Run check", () => {
  it("names each copied version as text", () => {
    const values = duplicate();
    expect(values.name).toBe("Model flows (copy)");
    expect(values.promptConfig.map((card) => card.versionLabel)).toEqual([
      "v1",
      "2",
    ]);
  });

  it("passes the form check and builds the create request for both runs", async () => {
    const values = duplicate();
    const parsed = await getNewExperimentValidationSchema(
      [],
      false,
      DATASET,
    ).safeParseAsync(values);
    expect(parsed.success, JSON.stringify(parsed.error?.issues ?? [])).toBe(
      true,
    );
    const wire = wireExperimentBody(
      experimentBody(parsed.data, values, { datasetId: DATASET }),
      {},
      false,
    );
    expect(wire.blocks).toEqual([]);
    expect(wire.body.name).toBe("Model flows (copy)");
    expect(wire.body.prompt_config).toHaveLength(2);
    expect(wire.body.prompt_config[0]).toMatchObject({
      prompt_version: prompt.prompt_version,
      model: prompt.model,
    });
    expect(wire.body.prompt_config[1]).toMatchObject({
      agent_id: agent.agent_id,
      agent_version: agent.agent_version,
    });
    expect(wire.body.user_eval_metrics).toHaveLength(1);
  });
});
