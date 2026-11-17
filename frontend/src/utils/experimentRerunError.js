import { enqueueSnackbar } from "notistack";
import { handleError } from "./queryErrorHandler";

export const RERUN_ADMISSION_UNCONFIRMED_MESSAGE =
  "Re-run admission was not confirmed for every experiment. Check execution status before retrying.";

// Rerun only: axios.js normalizes GeneralMethods' rejected error envelope to
// these top-level fields. Do not trim, coerce, flatten or display backend text.
export function handleExperimentRerunError(error) {
  if (
    error?.statusCode === 500 &&
    error?.status === false &&
    error?.type === "server_error" &&
    error?.code === "server_error" &&
    error?.detail === RERUN_ADMISSION_UNCONFIRMED_MESSAGE &&
    error?.message === RERUN_ADMISSION_UNCONFIRMED_MESSAGE &&
    error?.error === RERUN_ADMISSION_UNCONFIRMED_MESSAGE &&
    error?.result === RERUN_ADMISSION_UNCONFIRMED_MESSAGE
  ) {
    enqueueSnackbar(RERUN_ADMISSION_UNCONFIRMED_MESSAGE, { variant: "error" });
    return;
  }

  // MutationCache runs first, so callers mark meta.errorHandled up front.
  // Delegate EVERY other error without that skip marker; this preserves the
  // existing generic, gateway and rate-limit behavior without mutating errors.
  handleError(error);
}
