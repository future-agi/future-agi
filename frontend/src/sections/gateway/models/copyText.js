import { enqueueSnackbar } from "notistack";

// Clipboard access is missing on insecure origins (and in jsdom), so a copy
// button must not assume it.
export function copyText(text, message = "Copied to clipboard") {
  if (!navigator.clipboard?.writeText) {
    enqueueSnackbar("Copying is not available in this browser", {
      variant: "warning",
    });
    return;
  }
  navigator.clipboard
    .writeText(text)
    .then(() => enqueueSnackbar(message, { variant: "success" }))
    .catch(() =>
      enqueueSnackbar("Could not copy to clipboard", { variant: "error" }),
    );
}
