// L6 launch switch (L6 verify 2 must-fix): which New experiment settings the
// wizard's Configuration and Data steps show. A setting dev's experiment
// serializers do not honour sits under a hidden surface: the description
// (exp.rename), More settings (exp.new.record), Limits per row, the random
// sample and the rows a door preselected (exp.new.scope), the dataset
// version pin (dataset.versions) and the split chips (dataset.splits). With
// experimentsBasic ON a launch build shows New experiment without them.
//
// A hidden setting that already HOLDS a non-default value (a door's
// preselected rows, a copied experiment, a stale form) still shows, with
// its value: it is never dropped without a word. L2's serializer-derived
// checks (serverBody.js) block Run on it with the reason, and the control is
// there to clear it. Prototype builds and unit tests show everything.
import { isSurfaceShown } from "./launchSwitch";

/** The surface each wizard setting needs (registry ids). */
export const WIZARD_SETTING_SURFACE = Object.freeze({
  description: "exp.rename",
  moreSettings: "exp.new.record",
  rowLimits: "exp.new.scope",
  sample: "exp.new.scope",
  theseRows: "exp.new.scope",
  version: "dataset.versions",
  splits: "exp.new.splitScope",
});

/**
 * Whether a wizard setting shows: its surface ships, or it holds a value
 * that is not the default (then it shows so it can be seen and cleared).
 * @param {keyof typeof WIZARD_SETTING_SURFACE} setting
 * @param {boolean} [held]
 */
export function wizardSettingShown(setting, held = false) {
  const id = WIZARD_SETTING_SURFACE[setting];
  if (!id) throw new Error(`Unknown wizard setting: ${setting}`);
  return Boolean(held) || isSurfaceShown(id);
}

/** A text setting holds a value once it has non-blank text. Pure. */
export const heldText = (v) => String(v ?? "").trim().length > 0;

/** A list setting holds a value once it has an entry. Pure. */
export const heldList = (v) =>
  Array.isArray(v) && v.filter((x) => x != null && x !== "").length > 0;

/** An object of optional fields holds a value once one is set. Pure. */
export const heldFields = (v) =>
  Object.values(v ?? {}).some((x) => x != null && x !== "");
