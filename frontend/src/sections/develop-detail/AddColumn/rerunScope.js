// Generated-column saves keep their all-rows request body in real builds.
// A visible edit surface or an optional schema field must not opt saves into
// selective Results reruns. Scope choices remain available to prototype mocks.
import { isPrototypeMode } from "src/utils/prototype";

export const RERUN_OPERATION_BODY = "RerunOperationRequest";
export const EDIT_RUN_PROMPT_BODY = "EditRunPromptColumn";

export const RERUN_EVERY_ROW_LINE = "Save re-runs every row.";

/** Selective generated-column saves are a prototype-only interaction. */
export const rerunScopeSupported = (
  definition = RERUN_OPERATION_BODY,
  prototype = isPrototypeMode(),
) =>
  prototype &&
  [RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY].includes(definition);

/** Keep real edit bodies scope-free; attach scope only for prototype mocks. */
export function withRerunScope(
  body,
  scope,
  definition = RERUN_OPERATION_BODY,
  prototype = isPrototypeMode(),
) {
  if (!scope || !rerunScopeSupported(definition, prototype)) return body;
  return { ...body, scope };
}
