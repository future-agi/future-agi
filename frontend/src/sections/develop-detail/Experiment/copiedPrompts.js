// An experiment from before run snapshots keeps its prompts inline: the
// detail's inline_prompt_configs, one entry per prompt and model, its
// messages naming columns as a saved prompt does. Duplicate & edit runs them:
// one card per prompt, the prompt's models as the card's models. Saving the
// copy makes (or finds) a saved prompt for each prompt and model, named by
// the server's builder as copiedPromptName names it here.
import { getRandomId } from "src/utils/utils";
import { escapeModelKey, formModelParams } from "./modelParamNames";

// The inline prompt's own settings: sampling travels with each model's
// params (its own params win), tools with the card's configuration.
const SAMPLING = [
  "temperature",
  "frequency_penalty",
  "presence_penalty",
  "max_tokens",
  "top_p",
  "response_format",
];

const samplingOf = (configuration = {}) =>
  Object.fromEntries(
    SAMPLING.filter((key) => configuration?.[key] != null).map((key) => [
      key,
      configuration[key],
    ]),
  );

/** One run card per inline prompt of `detail` (none when it has none). */
export function cardsFromInlinePrompts(detail) {
  const byPrompt = new Map();
  (detail?.inline_prompt_configs ?? []).forEach((entry) => {
    const list = byPrompt.get(entry.prompt_index) ?? [];
    list.push(entry);
    byPrompt.set(entry.prompt_index, list);
  });
  return [...byPrompt.entries()].map(([promptIndex, entries]) => {
    const [first] = entries;
    const configuration = first.configuration ?? {};
    return {
      _itemId: getRandomId(),
      experimentType: "llm",
      targetType: "prompt",
      promptId: null,
      promptVersion: null,
      versionLabel: "",
      name: first.name,
      copyOf: {
        experimentId: String(detail.id),
        experimentName: detail.name ?? "",
        promptIndex,
      },
      copiedMessages: first.messages ?? [],
      model: entries.map((entry) => ({
        id: getRandomId(),
        value: entry.model,
        providers: entry.model_params?.providers ?? "",
        logoUrl: entry.model_params?.logo_url ?? "",
      })),
      modelParams: Object.fromEntries(
        entries.map((entry) => [
          escapeModelKey(entry.model),
          formModelParams({
            ...samplingOf(configuration),
            ...(entry.model_params ?? {}),
          }),
        ]),
      ),
      configuration: {
        toolChoice: configuration.tool_choice || "auto",
        tools: configuration.tools ?? [],
      },
      outputFormat: first.output_format ?? "string",
      isDraft: false,
      variables: [],
      variableMapping: {},
    };
  });
}

/**
 * Duplicate & edit of an experiment with inline prompts: the form's values
 * with its inline prompts as the copy's cards. Every door uses it, with
 * exp.new on or off; any other mode or experiment keeps `values` as it is.
 */
export function withInlinePromptCards(values, detail, { mode } = {}) {
  if (mode !== "duplicate" || !detail?.inline_prompt_configs?.length)
    return values;
  return { ...values, promptConfig: cardsFromInlinePrompts(detail) };
}

/** The saved prompt's name for one copied prompt and model. */
export const copiedPromptName = (experimentName, promptName, model) =>
  `[Migrated] ${experimentName} - ${promptName} - ${model}`;

/** The copy_of a copied card's run entries carry, else null. */
export const copyOfEntry = (card) =>
  card?.copyOf
    ? {
        experiment_id: card.copyOf.experimentId,
        prompt_index: card.copyOf.promptIndex,
      }
    : null;

/**
 * The sentence before the submit button while the form holds copied prompts:
 * what running it also saves to Prompts, by name. Null when there are none.
 */
export function copiedPromptsLine(cards = []) {
  const names = (cards ?? [])
    .filter((card) => card?.copyOf)
    .flatMap((card) =>
      (card.model ?? [])
        .filter((model) => model?.value)
        .map((model) =>
          copiedPromptName(card.copyOf.experimentName, card.name, model.value),
        ),
    );
  if (!names.length) return null;
  const quoted = names.map((name) => `“${name}”`).join(", ");
  return names.length === 1
    ? `Running this also saves its prompt to Prompts as ${quoted}.`
    : `Running this also saves its prompts to Prompts as ${quoted}.`;
}
