// Selective reruns require both the surface gate and the generated request
// contract. Older servers keep the all-rows body and its explicit explanation.
import { isSurfaceShown } from "src/launch/launchSwitch";
import { isPrototypeMode } from "src/utils/prototype";
import { unknownFields } from "src/api/contracts/strictBody";

export const RERUN_OPERATION_BODY = "RerunOperationRequest";
export const EDIT_RUN_PROMPT_BODY = "EditRunPromptColumn";

export const RERUN_EVERY_ROW_LINE = "Save re-runs every row.";

/** True where the route's body declares `scope` (or in prototype mode). */
export const rerunScopeSupported = (
  definition = RERUN_OPERATION_BODY,
  prototype = isPrototypeMode(),
) =>
  prototype ||
  (isSurfaceShown("dataset.runPromptEdit") &&
    unknownFields({ scope: "all" }, definition).length === 0);

/** The edit body, with `scope` only where the route takes it. */
export function withRerunScope(
  body,
  scope,
  definition = RERUN_OPERATION_BODY,
  prototype = isPrototypeMode(),
) {
  if (!scope || !rerunScopeSupported(definition, prototype)) return body;
  return { ...body, scope };
}
