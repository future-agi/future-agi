export const RERUN_OPERATION_BODY = "RerunOperationRequest";
export const EDIT_RUN_PROMPT_BODY = "EditRunPromptColumn";

export const RERUN_EVERY_ROW_LINE = "Save re-runs every row.";

/** Both generated-column edit endpoints select rows on the server. */
export const rerunScopeSupported = (definition = RERUN_OPERATION_BODY) =>
  [RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY].includes(definition);

/** Attach the selected scope only to edit requests that support it. */
export function withRerunScope(body, scope, definition = RERUN_OPERATION_BODY) {
  if (!scope || !rerunScopeSupported(definition)) return body;
  return { ...body, scope };
}
