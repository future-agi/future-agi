import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";

/** A copy notification describes the completed write, never just the click. */
export async function copyGridText(text, successMessage) {
  if (!(await copyToClipboard(text))) {
    enqueueSnackbar(
      "Couldn't copy. Clipboard access is unavailable or blocked.",
      { variant: "error" },
    );
    return false;
  }
  enqueueSnackbar(successMessage, { variant: "success" });
  return true;
}
