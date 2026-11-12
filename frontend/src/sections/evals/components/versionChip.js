// The version chip in an eval's detail header. A version picked on the page
// (the Versions panel, `?v=`, or the default the page loads into its form)
// names the chip. A typed eval (a decision tree, a remote scorer) is edited
// and versioned by its own editor (Save version, Save changes), never by the
// page's form, so its chip names the eval's current version, which moves
// when that editor saves (M10, W7 integration H-A1b; the remote walk c-remote2
// read "V1" beside the scorer's own "v2").
import { isTypedEvalType } from "src/sections/common/EvalTypes/typedEvalTypes";

/**
 * @param {{viewingVersion?: {version_number?: number}|null,
 *   evalData?: {current_version?: string}|null, evalType?: string}} args
 * @returns {string} e.g. "V2"
 */
export function versionChipOf({
  viewingVersion = null,
  evalData = null,
  evalType,
}) {
  if (viewingVersion?.version_number != null && !isTypedEvalType(evalType))
    return `V${viewingVersion.version_number}`;
  return evalData?.current_version || "V1";
}
