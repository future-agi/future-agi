// Duplicate & edit of an experiment from before run snapshots: each run of a
// copied card carries copy_of (copiedPrompts.js), which the server's create
// declares on a run entry (PromptConfigEntry.copy_of). The body the form sends
// keeps it and blocks nothing, so Run is not stopped on Review. The fields
// the server reads come from the generated contract (strictBody.js), so this
// holds once the contract is generated from the serializers that declare it.
import { describe, expect, it } from "vitest";
import { cardsFromInlinePrompts } from "../copiedPrompts";
import { experimentBodyOfValues } from "../experimentBody";
import { wireExperimentBody } from "../serverBody";
import { emptyExperimentValues } from "../experimentFormValues";

const MESSAGES = [{ role: "user", content: "Answer {{question}}" }];
const entry = (model, extra = {}) => ({
  name: "Prompt A",
  prompt_index: 0,
  model,
  model_display_name: null,
  model_params: {},
  configuration: { temperature: 0.2, tool_choice: "auto", tools: [] },
  output_format: "string",
  messages: MESSAGES,
  ...extra,
});
const DETAIL = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Old experiment",
  dataset_id: "22222222-2222-4222-8222-222222222222",
  prompt_configs: [],
  agent_configs: [],
  runs: [],
  inline_prompt_configs: [
    entry("gpt-4o-mini"),
    entry("gpt-4.1", { model_params: { temperature: 0.7, max_tokens: 50 } }),
  ],
};
const COLUMNS = [{ id: "c-question", name: "question" }];
const COPIED = { experiment_id: DETAIL.id, prompt_index: 0 };

const copyValues = () => ({
  ...emptyExperimentValues(),
  name: "Old experiment (copy)",
  promptConfig: cardsFromInlinePrompts(DETAIL),
  userEvalMetrics: [{ templateId: "t" }],
});

// The real backend's body, as the drawer builds it on every render.
const wireOf = (values) =>
  wireExperimentBody(
    experimentBodyOfValues(values, { datasetId: DETAIL.dataset_id }),
    { columns: COLUMNS },
    false,
  );

describe("the create body of a copied card", () => {
  it("raises no block for any field but copy_of", () => {
    const { blocks } = wireOf(copyValues());
    const others = blocks.filter((b) => b.field !== "prompt_config[].copy_of");

    expect(others).toEqual([]);
  });

  it("keeps copy_of on each run and blocks nothing", () => {
    const { body, blocks } = wireOf(copyValues());

    expect(blocks).toEqual([]);
    expect(body.prompt_config.map((e) => [e.model, e.copy_of])).toEqual([
      ["gpt-4o-mini", COPIED],
      ["gpt-4.1", COPIED],
    ]);
  });
});
