// Duplicate & edit of an experiment from before run snapshots, through the
// drawer's own checks. A copied card has no saved version until the copy is
// saved (copiedPrompts.js) and shows no version picker, so the step checks do
// not ask it for one; a card of a saved prompt is still asked. The form's
// check (schema.js), which builds the body Run sends, keeps the card's copy,
// so each run carries copy_of. The last case also reads the generated
// contract (strictBody.js): it holds once the contract is generated from the
// serializers that declare copy_of.
import { describe, expect, it } from "vitest";
import { cardsFromInlinePrompts } from "../copiedPrompts";
import { stepIssues } from "../stepIssues";
import { emptyExperimentValues } from "../experimentFormValues";
import { getNewExperimentValidationSchema } from "../schema";
import { experimentBody } from "../experimentBody";
import { wireExperimentBody } from "../serverBody";

const MESSAGES = [{ role: "user", content: "Answer {{question}}" }];
const entry = (model) => ({
  name: "Prompt A",
  prompt_index: 0,
  model,
  model_display_name: null,
  model_params: {},
  configuration: { temperature: 0.2, tool_choice: "auto", tools: [] },
  output_format: "string",
  messages: MESSAGES,
});
const DETAIL = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Old experiment",
  dataset_id: "22222222-2222-4222-8222-222222222222",
  inline_prompt_configs: [entry("gpt-4o-mini"), entry("gpt-4.1")],
};
const COLUMNS = [{ id: "c-question", name: "question" }];
const CTX = { nameTaken: false, columns: COLUMNS, isEditing: false };
const COPIED = { experiment_id: DETAIL.id, prompt_index: 0 };
// The evaluation a Duplicate copies from its source.
const EVALUATION = {
  templateId: "44444444-4444-4444-8444-444444444444",
  name: "Output check",
  config: { mapping: { output: "output" } },
};

const copyValues = (promptConfig = cardsFromInlinePrompts(DETAIL)) => ({
  ...emptyExperimentValues(),
  name: "Old experiment (copy)",
  promptConfig,
  userEvalMetrics: [EVALUATION],
});

const formCheck = (values) =>
  getNewExperimentValidationSchema(
    COLUMNS,
    false,
    DETAIL.dataset_id,
  ).safeParseAsync(values);

describe("the drawer's checks of a copied card", () => {
  it("find nothing to fix on any step", () => {
    expect(stepIssues(copyValues(), CTX)).toEqual([[], [], [], []]);
  });

  it("still ask a card with no copied prompt for its saved version", () => {
    const [card] = cardsFromInlinePrompts(DETAIL);
    const issues = stepIssues(copyValues([{ ...card, copyOf: null }]), CTX);

    expect(issues[0]).toContain("Pick a saved version of Prompt A");
  });

  it("keep copy_of on each run the form's check builds", async () => {
    const parsed = await formCheck(copyValues());

    expect(parsed.success, JSON.stringify(parsed.error?.issues ?? [])).toBe(
      true,
    );
    expect(parsed.data.promptConfig.map((e) => [e.model, e.copy_of])).toEqual([
      ["gpt-4o-mini", COPIED],
      ["gpt-4.1", COPIED],
    ]);
  });

  it("send copy_of with each run when Run is pressed", async () => {
    const values = copyValues();
    const parsed = await formCheck(values);
    const wire = wireExperimentBody(
      experimentBody(parsed.data, values, { datasetId: DETAIL.dataset_id }),
      { columns: COLUMNS },
      false,
    );

    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config.map((e) => [e.model, e.copy_of])).toEqual([
      ["gpt-4o-mini", COPIED],
      ["gpt-4.1", COPIED],
    ]);
  });
});
