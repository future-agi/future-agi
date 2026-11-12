// Why Run (or Save) is blocked on the real backend (the capability contract(b)): the
// form holds a setting dev's experiment serializers do not read. WHICH fields
// block is derived from the contract (serverBody.js + strictBody.js); this
// file only words them and says on which wizard step the setting lives.
// A field with no entry still blocks, with the generic line; the contract
// test (serverBody.contract.test.js) fails when a field the form can send
// has no words here.
import { EXPERIMENT_STEP } from "./constants";
import { a2aAuthProblem } from "src/sections/common/RunSources/endpoint/a2aMessage";

const S = EXPERIMENT_STEP;

const quote = (s) => `“${String(s ?? "").trim()}”`;

const kindLabel = (kind) =>
  ({
    http: "Endpoint",
    external: "Upload results",
    code: "Your code",
    traces: "Linked traces",
    harness: "Agent harness",
    voice: "Voice agent",
    a2a: "A2A agent",
    mcp_tool: "MCP tool",
  })[kind] ?? String(kind ?? "This");

const columnName = (id, columns = []) =>
  (columns ?? []).find((c) => c?.id === id)?.name ?? null;

/** {field id: {step, text(value, ctx)}}; nested ids read `prompt_config[].key`. */
export const SERVER_BLOCK_COPY = Object.freeze({
  // Top level of the create body.
  description: {
    step: S.CONFIGURATION,
    text: () =>
      "Descriptions aren't saved here. Clear the description to run the experiment.",
  },
  name: {
    step: S.CONFIGURATION,
    text: (_v, ctx) =>
      `Renaming an experiment isn't supported here. Keep the name ${quote(ctx?.initial?.name)} to save.`,
  },
  // With the Data step's version picker shown, one choice there clears it.
  dataset_version_id: {
    step: S.DATA,
    text: (_v, ctx) =>
      ctx?.currentRowsChoice
        ? `Running on an earlier dataset version isn't available in this release. Under Dataset version, choose ${quote("Current rows")} to run.`
        : "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.",
  },
  split_ids: {
    step: S.DATA,
    text: () =>
      "Changing the split isn't supported here. Choose all rows to run.",
  },
  row_ids: {
    step: S.DATA,
    text: (v) =>
      `Running on ${Array.isArray(v) ? v.length : "chosen"} picked rows isn't available in this release. Choose all rows to run.`,
  },
  sample: {
    step: S.DATA,
    text: () =>
      "Sampling rows isn't available in this release. Turn Sample off to run.",
  },
  limits: {
    step: S.CONFIGURATION,
    text: () =>
      "Per-row limits aren't available in this release. Clear the limits on each run to continue.",
  },
  trials: {
    step: S.REVIEW,
    text: (v) =>
      `${Number(v)} tries per row aren't supported here: each row runs once. Set Trials to 1 to run.`,
  },
  max_concurrency: {
    step: S.REVIEW,
    text: (v, ctx) =>
      `Running ${Number(v)} rows at once isn't supported here: the server runs ${ctx?.devRowsAtOnce ?? 10} at once. Set it to ${ctx?.devRowsAtOnce ?? 10} to run.`,
  },
  metadata: {
    step: S.REVIEW,
    text: () =>
      "Experiment metadata isn't saved here. Remove the metadata rows to run.",
  },
  error_policy: {
    step: S.REVIEW,
    text: () =>
      "Error-policy overrides aren't available in this release. Restore the default error policy to continue.",
  },
  trial_reducer: {
    step: S.REVIEW,
    text: () =>
      "Combining tries per row isn't supported here: each row runs once. Use Mean to run.",
  },
  record: {
    step: S.REVIEW,
    text: () =>
      "Runs kept off the leaderboard aren't supported. Turn Record on to run.",
  },
  preset: {
    step: S.CONFIGURATION,
    text: () =>
      "This preset isn't supported. Set the runs up here to run.",
  },
  carry_over_from_experiment_id: {
    step: S.CONFIGURATION,
    text: () =>
      "Carrying results over from an earlier experiment isn't supported: every row would run again. Turn carry-over off to run.",
  },
  seed_first_result: {
    step: S.CONFIGURATION,
    text: () =>
      "Reusing the try on one row as the first result isn't supported. Remove that run to start the experiment.",
  },
  // Inside one run (a prompt_config entry); dev drops these silently.
  "prompt_config[].target_type": {
    step: S.CONFIGURATION,
    text: (v) =>
      `${kindLabel(v)} runs aren't supported here. Remove that run, or use a prompt or an agent.`,
  },
  // A prompt or agent run fills {{name}} only from the column named `name`;
  // the words say how to get there (renameVariableText).
  "prompt_config[].variable_mapping": {
    step: S.DATA,
    text: (v, ctx) => {
      const [variable, target] =
        Object.entries(v ?? {}).find(
          ([name, value]) => !sameNameMapping(name, value, ctx?.columns),
        ) ?? [];
      return renameVariableText(
        variable ?? "variable",
        target,
        ctx?.columns,
        ctx?.kind,
      );
    },
  },
  "prompt_config[].tools": {
    step: S.CONFIGURATION,
    text: () =>
      "Tool answers (Mock, Recorded, Live) aren't supported: the model's tool call would be the output. Pick a prompt version without tools to run.",
  },
  "prompt_config[].tool_schemas": {
    step: S.CONFIGURATION,
    text: () =>
      "Tool answers (Mock, Recorded, Live) aren't supported: the model's tool call would be the output. Pick a prompt version without tools to run.",
  },
  "prompt_config[].max_tool_turns": {
    step: S.CONFIGURATION,
    text: () =>
      "Tool turns aren't supported here. Pick a prompt version without tools to run.",
  },
  "prompt_config[].on_unrecorded": {
    step: S.CONFIGURATION,
    text: () =>
      "Tool answers (Mock, Recorded, Live) aren't supported. Pick a prompt version without tools to run.",
  },
  "prompt_config[].row_limits": {
    step: S.CONFIGURATION,
    text: () =>
      "Different per-run row limits aren't supported. Set identical per-row limits on every run, or clear them all.",
  },
  "prompt_config[].limits": {
    step: S.CONFIGURATION,
    text: () =>
      "Per-run limits (tries, cost cap) aren't supported. Clear the run's limits to run.",
  },
  // An A2A agent run: what its card sets that the server does not send yet.
  "prompt_config[].a2a.card": {
    step: S.CONFIGURATION,
    text: () =>
      "The A2A agent run has no agent card. Discover the agent card, then run.",
  },
  "prompt_config[].a2a.text_column": {
    step: S.CONFIGURATION,
    text: () =>
      "Pick the column the A2A agent's message text comes from, then run.",
  },
  "prompt_config[].a2a.data_columns": {
    step: S.CONFIGURATION,
    text: () =>
      "Sending columns as a data part isn't available in this release. Clear Data part (JSON) from on the A2A agent run, then run.",
  },
  "prompt_config[].a2a.context_column": {
    step: S.CONFIGURATION,
    text: () =>
      "Continuing one A2A conversation across rows isn't available in this release. Clear the context column on the A2A agent run, then run.",
  },
  "prompt_config[].a2a.other": {
    step: S.CONFIGURATION,
    text: () =>
      "This A2A agent run carries settings of another run type, which an A2A agent doesn't take. Remove the run and add the A2A agent again, then run.",
  },
  "prompt_config[].a2a.auth": {
    step: S.CONFIGURATION,
    text: (v) =>
      a2aAuthProblem(v?.auth) === "Pick the workspace secret this auth sends."
        ? "Pick the workspace secret the A2A agent's auth sends, then run."
        : "Basic and OAuth 2 sign-in for A2A agents aren't available in this release. Choose Bearer token, API key header or None, then run.",
  },
});

/** The generic line for a field with no words above (it still blocks). */
export const genericBlockText = (field) =>
  `${String(field).replace(/^prompt_config\[\]\./, "")} isn't supported here. Remove it to run the experiment.`;

/** One block: {field, step, text, generic}. */
export function serverBlockOf(field, value, ctx = {}) {
  const c = SERVER_BLOCK_COPY[field];
  return {
    field,
    step: c ? c.step : EXPERIMENT_STEP.REVIEW,
    text: c ? c.text(value, ctx) : genericBlockText(field),
    generic: !c,
  };
}

/**
 * A variable that reads the column of exactly its own name, with no path:
 * what dev does without a mapping.
 */
export function sameNameMapping(variable, value, columns = []) {
  if (typeof value !== "string" || value.includes("::")) return false;
  return columnName(value, columns) === variable;
}

/**
 * What to do when a prompt or agent run's variable does not read the column
 * of its own name (the run fills {{name}} only from a column named `name`):
 * rename the variable to the picked column, or to one of the dataset's
 * columns, or add the column (pure).
 * @param {string} variable
 * @param {*} value  the variable's mapping (column id, path, empty or fixed)
 * @param {{id: string, name: string}[]} columns
 * @param {string} [kind]  the run's target type; "agent" words the agent
 */
export function renameVariableText(variable, value, columns = [], kind) {
  const v = `{{${variable}}}`;
  const where = kind === "agent" ? "in the agent" : "in the prompt";
  const names = (columns ?? []).map((c) => c?.name).filter(Boolean);
  const others = names.filter((n) => n !== variable);
  const picked =
    typeof value === "string" && !value.includes("::")
      ? columnName(value, columns)
      : null;
  const to =
    picked && picked !== variable
      ? `{{${picked}}}`
      : others.length
        ? `a column name (${others.slice(0, 3).join(", ")}${others.length > 3 ? ", …" : ""})`
        : null;
  const target = `${v} to ${to ?? "a column name"} ${where}`;
  const own = `Experiments fill ${v} only from a column named ${quote(variable)}.`;
  if (names.includes(variable))
    return `${own} Pick ${quote(variable)} for ${v}${to ? `, or rename ${target}` : ""}.`;
  // A dotted or indexed name would need a mapping to a path inside a column,
  // which a run here does not send.
  if (/[.[]/.test(variable))
    return `Mapping ${v} to a path inside a column isn't available in this release. Rename ${target}.`;
  return `${own} Rename ${target}, or add a column named ${quote(variable)} to the dataset.`;
}
