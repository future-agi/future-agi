// Duplicate & edit of an experiment from before run snapshots: its inline
// prompts (the detail's inline_prompt_configs) become the copy's cards, its
// runs carry copy_of so saving saves them as prompts, and the form says so
// before the button. A build with exp.new off copies them the same way.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  cardsFromInlinePrompts,
  copiedPromptName,
  copiedPromptsLine,
  withInlinePromptCards,
} from "../copiedPrompts";
import { cardsFromExperiment } from "../StartFromExperiment";
import { promptConfigTransform } from "../utils";
import { MODEL_TYPES } from "../../RunPrompt/common";

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

describe("a copy of an experiment from before run snapshots", () => {
  it("makes one card per prompt, with each of its models", () => {
    const [card, ...rest] = cardsFromInlinePrompts(DETAIL);

    expect(rest).toEqual([]);
    expect(card.name).toBe("Prompt A");
    expect(card.promptId).toBeNull();
    expect(card.promptVersion).toBeNull();
    expect(card.copyOf).toEqual({
      experimentId: DETAIL.id,
      experimentName: "Old experiment",
      promptIndex: 0,
    });
    expect(card.copiedMessages).toEqual(MESSAGES);
    expect(card.model.map((m) => m.value)).toEqual(["gpt-4o-mini", "gpt-4.1"]);
    // The prompt's settings: sampling with each model (its own params win),
    // tools with the card.
    expect(card.modelParams).toEqual({
      "gpt-4o-mini": { temperature: 0.2 },
      "gpt-4__DOT__1": { temperature: 0.7, maxTokens: 50 },
    });
    expect(card.configuration).toEqual({ toolChoice: "auto", tools: [] });
  });

  it("Duplicate & edit builds the copy's cards from them", () => {
    const values = cardsFromExperiment(DETAIL, {}, { mode: "duplicate" });

    expect(values.promptConfig).toHaveLength(1);
    expect(values.promptConfig[0].copyOf.promptIndex).toBe(0);
  });

  it("its runs carry copy_of, one per model, and no saved prompt", () => {
    const cards = cardsFromInlinePrompts(DETAIL);
    const entries = promptConfigTransform(cards, MODEL_TYPES.LLM, "string");

    const rows = entries.map((e) => [e.model, e.prompt_id, e.prompt_version]);
    expect(rows).toEqual([
      ["gpt-4o-mini", null, null],
      ["gpt-4.1", null, null],
    ]);
    entries.forEach((e) =>
      expect(e.copy_of).toEqual({ experiment_id: DETAIL.id, prompt_index: 0 }),
    );
  });

  it("says before the button what running it saves to Prompts", () => {
    const cards = cardsFromInlinePrompts(DETAIL);
    const one = cardsFromInlinePrompts({
      ...DETAIL,
      inline_prompt_configs: [entry("gpt-4o-mini")],
    });

    expect(copiedPromptsLine(one)).toBe(
      "Running this also saves its prompt to Prompts as “[Migrated] Old experiment - Prompt A - gpt-4o-mini”.",
    );
    expect(copiedPromptsLine(cards)).toBe(
      "Running this also saves its prompts to Prompts as “[Migrated] Old experiment - Prompt A - gpt-4o-mini”, “[Migrated] Old experiment - Prompt A - gpt-4.1”.",
    );
    expect(copiedPromptsLine([{ name: "Saved", model: [] }])).toBeNull();
  });

  it("names a saved prompt as the server's builder does", () => {
    expect(copiedPromptName("Old experiment", "Prompt A", "gpt-4o")).toBe(
      "[Migrated] Old experiment - Prompt A - gpt-4o",
    );
  });
});

const RUN_EXPERIMENT = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "RunExperiment.jsx"),
  "utf8",
);

describe("Duplicate & edit with exp.new off", () => {
  const values = { name: "Old experiment (copy)", promptConfig: [] };

  it("carries the inline prompts as the copy's cards", () => {
    const copied = withInlinePromptCards(values, DETAIL, { mode: "duplicate" });

    expect(copied.name).toBe("Old experiment (copy)");
    expect(copied.promptConfig).toHaveLength(1);
    expect(copied.promptConfig[0].copyOf).toEqual({
      experimentId: DETAIL.id,
      experimentName: "Old experiment",
      promptIndex: 0,
    });
    expect(copied.promptConfig[0].model.map((m) => m.value)).toEqual([
      "gpt-4o-mini",
      "gpt-4.1",
    ]);
  });

  it("leaves Edit, and an experiment without inline prompts, alone", () => {
    const none = { ...DETAIL, inline_prompt_configs: [] };

    expect(withInlinePromptCards(values, DETAIL, { mode: "edit" })).toBe(
      values,
    );
    expect(withInlinePromptCards(values, none, { mode: "duplicate" })).toBe(
      values,
    );
  });

  it("is what RunExperiment's classic fill passes through", () => {
    expect(RUN_EXPERIMENT).toMatch(
      /return hydrateHttpSourceValues\(\s*withInlinePromptCards\(/,
    );
  });
});
