import { enqueueSnackbar } from "notistack";
import { RESPONSE_CODES } from "./constants";
import { getSafeActionErrorMessage } from "./errorUtils";

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
