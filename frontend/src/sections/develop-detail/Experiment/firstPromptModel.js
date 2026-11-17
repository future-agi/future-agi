// A first prompt written in the New experiment wizard (JRN-EXP-NO-PROMPT-DEADEND).
// A brand-new workspace has no saved prompt, and an LLM run on dev's V2
// create must name a saved prompt version: every non-agent entry is looked
// up by `prompt_version` and a draft is refused
// (model_hub/views/experiments.py:4331-4380, 4500-4507); inline `messages`
// are read for tts / stt / image runs only
// (model_hub/serializers/experiments.py:856). So the prompt is saved as a
// real prompt first: create-draft with `is_draft: false` stores a saved v1
// (model_hub/views/prompt_template.py:1201, 1277-1286) and answers with the
// prompt's id and name (:1291-1294), which is all a run card needs.
// `{{column}}` is filled from the row's column of that name
// (model_hub/views/run_prompt.py:491-492), and the wizard maps a variable to
// the column of the same name (RunExperiment.jsx autoMapVariables).
// Pure: values in, words and bodies out.
import { variablesInTemplate } from "./variableMapping";

/** `{{customer tier}}` for the column `customer tier`. */
export const variableToken = (name) => `{{${name}}}`;

/** The name a new prompt starts with: `<dataset> prompt`. */
export function defaultPromptName(datasetName) {
  const base = String(datasetName ?? "").trim();
  return base ? `${base} prompt` : "First prompt";
}

/** The message a new prompt starts with: the dataset's input column. */
export function starterMessage(inputColumn) {
  return inputColumn ? variableToken(inputColumn) : "";
}

/**
 * `token` put at [start, end) of `text` (the end when there is no caret),
 * with a space before it when it would touch a word.
 * @returns {{text: string, caret: number}}
 */
export function insertToken(text, token, start, end) {
  const value = String(text ?? "");
  const from = Number.isInteger(start)
    ? Math.max(0, Math.min(start, value.length))
    : value.length;
  const to = Number.isInteger(end)
    ? Math.max(from, Math.min(end, value.length))
    : from;
  const before = value.slice(0, from);
  const pad = before && !/\s$/.test(before) ? " " : "";
  return {
    text: `${before}${pad}${token}${value.slice(to)}`,
    caret: before.length + pad.length + token.length,
  };
}

const modelNameOf = (model) => String(model?.model_name ?? "").trim();

/** What the prompt still needs before it can be saved. */
export function firstPromptIssues({ name, model, message }) {
  const issues = [];
  if (!String(name ?? "").trim()) issues.push("Give the prompt a name");
  if (!modelNameOf(model)) issues.push("Pick a model");
  if (!String(message ?? "").trim()) issues.push("Write the message to send");
  return issues;
}

/**
 * `{{name}}`s that are not a column of this dataset. A run fills a variable
 * only from the column of the same name; any other mapping is refused for a
 * prompt run (serverBody.js `prompt_config[].variable_mapping`), so such a
 * prompt could not run here.
 */
export function unknownVariables(texts = [], columns = []) {
  const names = new Set((columns ?? []).map((c) => String(c?.name ?? "")));
  const out = [];
  (texts ?? []).forEach((text) =>
    variablesInTemplate(text).forEach((v) => {
      if (!names.has(v) && !out.includes(v)) out.push(v);
    }),
  );
  return out;
}

/** The line that names them, or "" when there are none. */
export function unknownVariablesLine(unknown = []) {
  if (!unknown?.length) return "";
  const tokens = unknown.map(variableToken).join(", ");
  return unknown.length === 1
    ? `${tokens} isn't a column in this dataset. Pick one from the list below.`
    : `${tokens} aren't columns in this dataset. Pick one from the list below.`;
}

const textMessage = (role, text) => ({
  role,
  content: [{ type: "text", text }],
});

/**
 * The create-draft body of a saved prompt: optional instructions (system),
 * the message (user) and the picked model, in the shape Prompts saves
 * (gateway/playground/handoff.js draftBody). `model_detail.type` travels
 * only when the model list gave one: the Prompt picker lists `chat`
 * prompts, and a missing type counts as chat while "" matches nothing
 * (model_hub/views/prompt_template.py:4170-4186, custom-model-dropdown
 * common.js normalizeModelOption).
 */
export function firstPromptBody({ name, model, instructions, message }) {
  const system = String(instructions ?? "").trim();
  const modelName = modelNameOf(model);
  return {
    name: String(name ?? "").trim(),
    is_draft: false,
    prompt_config: [
      {
        messages: [
          ...(system ? [textMessage("system", system)] : []),
          textMessage("user", String(message ?? "").trim()),
        ],
        configuration: {
          model: modelName,
          model_detail: {
            model_name: modelName,
            providers: model?.providers ?? "",
            logo_url: model?.logoUrl ?? "",
            is_available: Boolean(model?.isAvailable),
            ...(model?.type ? { type: model.type } : {}),
          },
        },
      },
    ],
  };
}

/**
 * The saved prompt as the run card reads it (ClassicConfigureLLM promptCard),
 * from the create-draft answer; null when it names no prompt.
 */
export function savedPromptOf(result, model, typedName) {
  const id = result?.id ?? result?.root_template ?? null;
  if (!id) return null;
  return {
    id,
    name: result?.name || typedName,
    model: modelNameOf(model),
    model_detail: {
      logo_url: model?.logoUrl ?? "",
      providers: model?.providers ?? "",
    },
  };
}
