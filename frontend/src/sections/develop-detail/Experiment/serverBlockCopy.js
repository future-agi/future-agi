// Why Run (or Save) is blocked on the real backend (the capability contract(b)): the
// form holds a setting dev's experiment serializers do not read. WHICH fields
// block is derived from the contract (serverBody.js + strictBody.js); this
// file only words them and says on which wizard step the setting lives.
// A field with no entry still blocks, with the generic line; the contract
// test (serverBody.contract.test.js) fails when a field the form can send
// has no words here.
import { EXPERIMENT_STEP } from "./constants";

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
  dataset_version_id: {
    step: S.DATA,
    text: () =>
      "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.",
  },
  split_ids: {
    step: S.DATA,
    text: () =>
      "Changing the split isn't supported here. Choose all rows to run.",
  },
  row_ids: {
    step: S.DATA,
    text: (v) =>
      `Running on ${Array.isArray(v) ? v.length : "chosen"} picked rows isn't supported in this build. Choose all rows to run.`,
  },
  sample: {
    step: S.DATA,
    text: () =>
      "Sampling rows isn't supported in this build. Turn Sample off to run.",
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
  "prompt_config[].variable_mapping": {
    step: S.DATA,
    text: (v, ctx) => {
      const [variable, target] =
        Object.entries(v ?? {}).find(
          ([name, value]) => !sameNameMapping(name, value, ctx?.columns),
        ) ?? [];
      const col =
        typeof target === "string"
          ? columnName(target.split("::")[0], ctx?.columns)
          : null;
      const reads =
        target && typeof target === "object"
          ? target.kind === "constant"
            ? "a fixed value"
            : "nothing"
          : col
            ? `column ${quote(col)}${String(target).includes("::") ? " at a path" : ""}`
            : "another column";
      return `{{${variable ?? "variable"}}} reads ${reads}. Mapping a variable isn't supported here: the server fills {{${variable ?? "variable"}}} using the template path ${quote(variable)}.`;
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
