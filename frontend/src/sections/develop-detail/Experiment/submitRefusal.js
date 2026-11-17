// Why Run experiment sent nothing, as one plain line by the
// button (RunExperiment.jsx). Before, a refused form check only raised a
// short toast and a block only the submitted body carried showed nowhere,
// so Run looked dead. Pure: errors / blocks in, `{step, text}` out.
import { EXPERIMENT_STEP } from "./constants";

const S = EXPERIMENT_STEP;

/** The wizard step each form field is set on. */
const STEP_OF_FIELD = Object.freeze({
  name: S.CONFIGURATION,
  description: S.CONFIGURATION,
  experimentType: S.CONFIGURATION,
  outputFormat: S.CONFIGURATION,
  columnId: S.CONFIGURATION,
  promptConfig: S.CONFIGURATION,
  datasetVersionId: S.DATA,
  splitIds: S.DATA,
  rowIds: S.DATA,
  userEvalMetrics: S.EVALUATIONS,
  trials: S.REVIEW,
  maxConcurrency: S.REVIEW,
  metadata: S.REVIEW,
});

const FIELD_NAME = Object.freeze({
  name: "The name",
  description: "The description",
  experimentType: "The model type",
  outputFormat: "The output format",
  columnId: "The reference column",
  datasetVersionId: "The dataset version",
  splitIds: "The chosen splits",
  rowIds: "The chosen rows",
  userEvalMetrics: "An evaluation",
  trials: "Trials",
  maxConcurrency: "Rows at once",
  metadata: "Metadata",
});

// The check's own wording ("Expected string, received number", "Required",
// "Invalid input") is not for users; the form's written messages are.
const BUILT_IN_MESSAGE =
  /^(Expected\b|Required$|Invalid\b|Number must\b|String must\b|Array must\b|Unrecognized\b)/;

/** Shown when the check itself fails before anything is sent. */
export const SUBMIT_CHECK_FAILED =
  "Could not check this experiment. Reload the page and try again.";

/** Shown when the check refused but named no field. */
export const SETTINGS_NEED_ATTENTION =
  "Some settings need attention. Check each step, then run again.";

const isLeaf = (node) =>
  Boolean(node) &&
  typeof node === "object" &&
  typeof node.message === "string" &&
  typeof node.type === "string";

/** The first refused field of react-hook-form's `errors`: {path, message}. */
export function firstFieldError(node, path = []) {
  if (!node || typeof node !== "object") return null;
  if (isLeaf(node))
    return node.message.trim() ? { path, message: node.message } : null;
  const keys = Array.isArray(node)
    ? [...node.keys()]
    : Object.keys(node).filter((k) => k !== "ref" && k !== "types");
  for (const key of keys) {
    const found = firstFieldError(node[key], [...path, key]);
    if (found) return found;
  }
  return null;
}

/**
 * The line for a form check that refused the values (handleSubmit's invalid
 * callback). Written messages pass through; a built-in one becomes a plain
 * line naming the run or the setting.
 * @param {object} errors  react-hook-form errors
 * @param {object} [values]  getValues(), to name the run
 * @returns {{step: number, text: string}}
 */
export function refusalOfErrors(errors, values = {}) {
  const found = firstFieldError(errors);
  if (!found) return { step: S.REVIEW, text: SETTINGS_NEED_ATTENTION };
  const [field, index] = found.path;
  const step = STEP_OF_FIELD[field] ?? S.REVIEW;
  if (!BUILT_IN_MESSAGE.test(found.message.trim()))
    return { step, text: found.message.trim() };
  if (field === "promptConfig") {
    const card = Number.isInteger(index) ? values?.promptConfig?.[index] : null;
    const name = String(card?.name ?? card?.runName ?? "").trim();
    const run = name ? `The run “${name}”` : "A run";
    return {
      step,
      text: `${run} has a setting this form can't read. Remove the run and add it again.`,
    };
  }
  return {
    step,
    text: `${FIELD_NAME[field] ?? "A setting"} is not valid. Check it and try again.`,
  };
}

/**
 * The line for a server block of the submitted body that the step's own
 * issues do not already show (they show on that step). Null when every block
 * is already listed.
 * @param {{step?: number, text: string}[]} blocks
 * @param {string[][]} issues  the wizard's issues per step
 * @returns {{step: number, text: string}|null}
 */
export function refusalOfBlocks(blocks = [], issues = []) {
  const last = Math.max(0, (issues?.length ?? 1) - 1);
  for (const block of blocks ?? []) {
    const step = Math.min(Math.max(0, block?.step ?? last), last);
    if (block?.text && !(issues?.[step] ?? []).includes(block.text))
      return { step, text: block.text };
  }
  return null;
}

/** `Not ready: Configuration · …` (steps: experimentCreationSteps). */
export function refusalLine(refusal, steps = []) {
  if (!refusal?.text) return "";
  const title = steps?.[refusal.step]?.title;
  return title
    ? `Not ready: ${title} · ${refusal.text}`
    : `Not ready: ${refusal.text}`;
}
