import { enqueueSnackbar } from "notistack";
import {
  isWorkspaceRecoveryHandled,
  keepForUnexplainedRecovery,
} from "src/contexts/workspaceRefusalHold";
import { RESPONSE_CODES } from "./constants";
import {
  getGatewayAuthenticationErrorMessage,
  getSafeActionErrorMessage,
} from "./errorUtils";
import {
  MIXED_RUNS_TOAST_KEY,
  MIXED_RUNS_WORDS,
  isMixedRunsRefusal,
} from "./mixedRunsRefusal";

const _extractParts = (result) => {
  if (result == null || result === "") return "";
  if (typeof result === "string") return result;
  if (Array.isArray(result)) {
    return [...new Set(result.map(_extractParts).filter(Boolean))].join(", ");
  }
  if (typeof result === "object") {
    if (result.details && typeof result.details === "object") {
      return _extractParts(result.details);
    }
    return [
      ...new Set(Object.values(result).map(_extractParts).filter(Boolean)),
    ].join(", ");
  }
  return String(result);
};

const extractErrorMessage = (result) =>
  _extractParts(result) || "Something went wrong";

// The app's QueryCache and MutationCache onError: toasts a failed read or
// write unless its caller reports the failure itself (meta.errorHandled).
export const handleError = (error, variable, context, mutation) => {
  if (error?.statusCode == RESPONSE_CODES.LIMIT_REACHED) return;
  if (
    mutation?.options?.meta?.errorHandled ||
    variable?.options?.meta?.errorHandled
  )
    return;
  // The workspace recovery explains this refusal (one info line). If it ends
  // without a line of its own, the usual toast is shown after all.
  if (isWorkspaceRecoveryHandled(error)) {
    const unhandled = { ...error };
    delete unhandled.handledBy;
    keepForUnexplainedRecovery(() =>
      handleError(unhandled, variable, context, mutation),
    );
    return;
  }
  const gatewayRemedy = getGatewayAuthenticationErrorMessage(error);
  if (gatewayRemedy) {
    enqueueSnackbar(gatewayRemedy, { variant: "error" });
    return;
  }
  // Runs that can't be lined up (different dataset version, trial plan or
  // evaluators): plain words, one toast at a time.
  if (isMixedRunsRefusal(error)) {
    enqueueSnackbar(MIXED_RUNS_WORDS, {
      variant: "error",
      key: MIXED_RUNS_TOAST_KEY,
      preventDuplicate: true,
    });
    return;
  }
  if (error?.result) {
    const message = getSafeActionErrorMessage(
      {
        statusCode: error?.statusCode,
        code: error?.code,
        result: extractErrorMessage(error.result),
      },
      "Something went wrong",
    );
    enqueueSnackbar(message, {
      variant: "error",
    });
  }
};
