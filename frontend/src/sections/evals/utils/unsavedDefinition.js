// The eval editor's Test runs what is on screen and saves nothing: the
// editor's fields travel in the eval playground request's config under
// `unsaved_definition`, and the backend runs an in-memory copy of the
// evaluation with them (model_hub/utils/unsaved_eval_definition.py). Save
// Version stays the only way to save them.

export const UNSAVED_DEFINITION_KEY = "unsaved_definition";

/** The playground config entry for an unsaved definition ({} for none). */
export const unsavedDefinitionConfig = (definition) =>
  definition ? { [UNSAVED_DEFINITION_KEY]: definition } : {};

/**
 * The tooltip on "Testing unsaved changes". It says only what is true for
 * every evaluation type: Test runs the unsaved edits and saves nothing, and
 * Save Version keeps them. The evaluation type and judge do not change it.
 */
// eslint-disable-next-line no-unused-vars
export function unsavedTestTooltip({ isComposite, evalType, isJevModel }) {
  if (isComposite) {
    return "Test runs what is on screen without saving it. Save Changes keeps it for experiments and runs.";
  }
  return "Test runs your unsaved edits without saving them. Save Version keeps your edits for experiments and runs.";
}

/**
 * The editor's fields as the template update takes them: unset fields are
 * left out, and so is a blank model (none picked yet), which the update
 * refuses.
 */
export function compactDefinition(fields = {}) {
  return Object.fromEntries(
    Object.entries(fields).filter(
      ([key, value]) => value !== undefined && !(key === "model" && !value),
    ),
  );
}
