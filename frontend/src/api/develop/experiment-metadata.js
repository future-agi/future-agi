// exp.rename: an experiment's name and description. Dev saves them with a
// metadata-only PUT experiments/v2/{id}/ (`{name}`, `{description}` or
// both, never next to run settings); the runs, their results and their own
// run names stay as they are. This module holds the plain words the rename
// controls show and the reads a saved rename refreshes.
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { datasetRunsKey } from "./runs";
import { runResultsKey } from "./run-results";

/** Dev's limits (ExperimentNameField / ExperimentDescriptionField). */
export const EXPERIMENT_NAME_MAX = 255;
export const EXPERIMENT_DESCRIPTION_MAX = 4000;

export const RENAME_COPY = Object.freeze({
  emptyName: "Enter an experiment name.",
  longName: `Use ${EXPERIMENT_NAME_MAX} characters or fewer for the experiment name.`,
  duplicateName:
    "An experiment with this name already exists in this dataset. Choose another name.",
  longDescription: `Use ${EXPERIMENT_DESCRIPTION_MAX} characters or fewer for the description.`,
  denied: "You don't have permission to edit experiments in this workspace.",
  gone: "This experiment no longer exists, or you no longer have access to it.",
  unconfirmed:
    "We couldn't confirm the save. Check the experiment in the list before you try again.",
  failed: "Could not save the changes. Try again.",
});

const FIELDS = ["name", "description"];

// Dev counts characters (Python len), not UTF-16 code units: an emoji is one
// character there and two in String.length.
export const charCount = (text) => Array.from(String(text ?? "")).length;

/** What is wrong with a name draft before it is sent, or null. Pure. */
export function nameProblem(draft) {
  const text = String(draft ?? "").trim();
  if (!text) return RENAME_COPY.emptyName;
  if (charCount(text) > EXPERIMENT_NAME_MAX) return RENAME_COPY.longName;
  return null;
}

/** What is wrong with a description draft before it is sent, or null. Pure. */
export function descriptionProblem(draft) {
  return charCount(draft) > EXPERIMENT_DESCRIPTION_MAX
    ? RENAME_COPY.longDescription
    : null;
}

const plainText = (value) => {
  const text = Array.isArray(value) ? value[0] : value;
  if (typeof text !== "string") return null;
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 240 || /[\r\n]/.test(trimmed)) return null;
  return trimmed;
};

/**
 * What a refused or unconfirmed rename says, in plain words. Pure.
 * `fields` holds dev's words for Name / Description (shown next to the
 * field); otherwise `message` is one line for the whole form. `unknown` is
 * true when no answer came back (network, timeout, server error): the save
 * may have landed, so the caller reads the lists again.
 * @param {object} error  the rejected request (src/utils/axios error shape)
 * @returns {{fields: {name?: string, description?: string},
 *   message: string|null, unknown: boolean}}
 */
export function renameErrorOf(error) {
  const data = error?.response?.data ?? error ?? {};
  const status =
    Number(error?.response?.status ?? error?.statusCode ?? data?.statusCode) ||
    0;
  const fields = {};
  FIELDS.forEach((key) => {
    const text = plainText(data?.details?.[key]);
    if (text) fields[key] = text;
  });
  if (!Object.keys(fields).length && FIELDS.includes(data?.attr)) {
    const text = plainText(data?.detail)?.replace(/^\w+:\s*/, "");
    if (text) fields[data.attr] = text;
  }
  if (Object.keys(fields).length)
    return { fields, message: null, unknown: false };
  if (!status || status >= 500)
    return { fields, message: RENAME_COPY.unconfirmed, unknown: true };
  if (status === 403)
    return {
      fields,
      message:
        plainText(data?.detail) ??
        plainText(data?.message) ??
        RENAME_COPY.denied,
      unknown: false,
    };
  if (status === 404)
    return { fields, message: RENAME_COPY.gone, unknown: false };
  return {
    fields,
    message: getSafeActionErrorMessage(error, RENAME_COPY.failed),
    unknown: false,
  };
}

/** One line for a toast: a field's words first, else the form's. Pure. */
export const renameErrorText = (failure) =>
  failure?.fields?.name ??
  failure?.fields?.description ??
  failure?.message ??
  RENAME_COPY.failed;

/**
 * The success toast of a rename: the name dev saved (trimmed by dev), not
 * the draft. Pure.
 * @param {object} res   the PUT response
 * @param {{name?: string, description?: string}} sent  what the PUT carried
 */
export function renameSuccessText(res, sent = {}) {
  if (sent?.name !== undefined) {
    const saved = res?.data?.result?.name;
    const name =
      typeof saved === "string" && saved.trim()
        ? saved
        : String(sent.name).trim();
    return `Renamed to “${name}”`;
  }
  return sent?.description === "" ? "Description cleared" : "Description saved";
}

/**
 * Everything that shows the experiment's name or description reads again:
 * the leaderboard and run lists (R1), the Results grid run headers, the
 * experiment page, the classic list, the dataset list and the name checks.
 */
export function refreshAfterRename(queryClient, { datasetId, experimentId }) {
  if (!queryClient) return;
  const keys = [
    datasetRunsKey(datasetId),
    runResultsKey(datasetId),
    ["experiment-list"],
    ["datasets", "list"],
    ["experiment-validate-name", datasetId],
    ["experiment-suggested-name", datasetId],
  ];
  if (experimentId) keys.push(["experiment", experimentId]);
  keys.forEach((queryKey) => queryClient.invalidateQueries({ queryKey }));
}
