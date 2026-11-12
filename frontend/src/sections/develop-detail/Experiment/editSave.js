// exp.rename: what an Edit save of the wizard sends. Dev saves the name and
// description on their own (a body that mixes them with run settings answers
// 400), so a save is up to two PUTs on experiments/v2/{id}/: the changed
// name / description first (`metadata`), then the run settings (`config`)
// when something about the runs changed. A rename alone never sends run
// settings, so it never starts a run. Pure, apart from runEditSave, which
// runs the two saves it is given in that order.
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { experimentBodyOfValues } from "./experimentBody";
import { withHttpSourceSettings } from "./serverBody";

const settingsKey = (values, datasetId) => {
  // The same body the drawer sends, HTTP endpoint settings included.
  const body = {
    ...withHttpSourceSettings(
      experimentBodyOfValues(values ?? {}, { isEditing: true, datasetId }),
      values?.promptConfig ?? [],
    ),
  };
  delete body.name;
  delete body.description;
  return JSON.stringify([body, values?.userEvalMetrics ?? []]);
};

/**
 * Whether the form's run settings (runs, evals, reference column) differ
 * from the experiment's. Any difference counts, so an unsure answer sends
 * the run settings as before (dev re-runs only what its own diff finds).
 * @param {object} original  the form values the edit opened with
 * @param {object} current   getValues()
 */
export function editSettingsChanged(original, current, { datasetId } = {}) {
  return settingsKey(original, datasetId) !== settingsKey(current, datasetId);
}

/**
 * @param {{body: object, metadata?: object|null}} wire  wireExperimentBody()
 * @param {{original: object, current: object, datasetId?: string}} ctx
 * @returns {{metadata: object|null, config: object|null}} config is null
 *   only when the save changes the name or description alone.
 */
export function editSavePlan(wire, { original, current, datasetId } = {}) {
  const metadata =
    wire?.metadata && Object.keys(wire.metadata).length ? wire.metadata : null;
  const config = wire?.body ?? {};
  if (!metadata) return { metadata: null, config };
  return {
    metadata,
    config: editSettingsChanged(original, current, { datasetId })
      ? config
      : null,
  };
}

/**
 * The names an Edit treats as the experiment's own, so its name check never
 * calls them taken: the name a save already saved (else the one the edit
 * opened with), and a name sent without an answer (that save may have
 * landed). Trimmed, as dev compares them. Pure.
 * @param {{opened?: string, saved?: string, unconfirmed?: string|null}} names
 * @returns {string[]}
 */
export function ownNamesOf({ opened, saved, unconfirmed } = {}) {
  return [saved ?? opened, unconfirmed]
    .filter((name) => name != null)
    .map((name) => String(name).trim());
}

/**
 * Runs an Edit save in order: the name / description first (when the plan
 * has them), then the run settings (when the plan has them). A refused name
 * or description stops there, so no run settings follow a name dev did not
 * take. Each step returns the request's promise. Never rejects.
 * @param {{metadata?: object|null, config?: object|null}} plan  editSavePlan()
 * @param {{saveMetadata: (body: object) => Promise<any>,
 *   saveSettings: (body: object) => Promise<any>}} steps
 * @returns {Promise<{done: ("metadata"|"settings")[],
 *   failed: {step: "metadata"|"settings", error: any}|null,
 *   renamed: any, saved: any}>}  done: the saves dev took, in order;
 *   renamed / saved: dev's answer to each.
 */
export async function runEditSave(plan, { saveMetadata, saveSettings }) {
  const outcome = { done: [], failed: null, renamed: null, saved: null };
  const { metadata, config } = plan ?? {};
  if (metadata) {
    try {
      outcome.renamed = await saveMetadata(metadata);
      outcome.done.push("metadata");
    } catch (error) {
      outcome.failed = { step: "metadata", error };
      return outcome;
    }
  }
  if (config) {
    try {
      outcome.saved = await saveSettings(config);
      outcome.done.push("settings");
    } catch (error) {
      outcome.failed = { step: "settings", error };
    }
  }
  return outcome;
}

/**
 * Puts dev's words for a refused name or description on the form field
 * itself (FormTextFieldV2 shows errors.<field>.message under the field), so
 * the reason stays where the user fixes it. Editing the field clears it.
 * @param {{fields?: {name?: string, description?: string}}} failure
 *   renameErrorOf()
 * @param {(field: string, error: {type: string, message: string}) => void}
 *   setError  react-hook-form's setError
 * @returns {boolean} whether a field took the words (else the caller says
 *   the form-level reason)
 */
export function renameFieldErrors(failure, setError) {
  const entries = Object.entries(failure?.fields ?? {});
  entries.forEach(([field, message]) =>
    setError(field, { type: "server", message }),
  );
  return entries.length > 0;
}

/**
 * The error toast when the run settings were not saved. After a saved
 * rename it says the new name is kept, so the user knows what landed.
 * @param {object} error   the rejected settings PUT
 * @param {string|null} [kept]  renameSuccessText() of the saved rename
 */
export function settingsErrorText(error, kept = null) {
  if (!kept)
    return getRequestErrorMessage(error, "Could not save the experiment.");
  const reason = getRequestErrorMessage(error, "Try again.");
  return `${kept}. The run settings were not saved: ${reason}`;
}
