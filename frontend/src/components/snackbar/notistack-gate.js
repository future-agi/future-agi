// Every `notistack` import in the app resolves to this module (the
// notistackGate plugin in vite.config.js); only this file's own import below
// reaches the package. That makes it the one place every toast passes
// through, whether a screen imports notistack directly, uses useSnackbar, or
// goes through src/components/snackbar.
//
// It drops a toast whose text is a workspace refusal that the workspace
// recovery is already explaining (contexts/workspaceRefusalHold.js), and
// shows it after all if that recovery ends without a line of its own.
import { isValidElement, useMemo } from "react";
import * as notistack from "notistack";
import {
  isHeldRefusalToast,
  keepForUnexplainedRecovery,
} from "src/contexts/workspaceRefusalHold";

export {
  MaterialDesignContent,
  SnackbarContent,
  SnackbarProvider,
  Transition,
} from "notistack";

let droppedCount = 0;

// enqueueSnackbar({ message, ...options }) rather than (message, options).
const isOptionsObject = (messageOrOptions) =>
  Boolean(messageOrOptions) &&
  typeof messageOrOptions === "object" &&
  !isValidElement(messageOrOptions);

function isDropped(messageOrOptions) {
  const message = isOptionsObject(messageOrOptions)
    ? messageOrOptions.message
    : messageOrOptions;
  return isHeldRefusalToast(message);
}

// A defined key that matches no toast: notistack closes every toast when
// closeSnackbar gets undefined, so a caller closing this key must not.
function droppedKey(messageOrOptions, options) {
  droppedCount += 1;
  return (
    options?.key ??
    (typeof messageOrOptions === "object" ? messageOrOptions?.key : undefined) ??
    `dropped-refusal-${droppedCount}`
  );
}

// A replay keeps the key returned at drop time, so the caller's
// closeSnackbar(key) still closes it.
function drop(enqueue, messageOrOptions, options) {
  const key = droppedKey(messageOrOptions, options);
  keepForUnexplainedRecovery(() =>
    isOptionsObject(messageOrOptions)
      ? enqueue({ ...messageOrOptions, key })
      : enqueue(messageOrOptions, { ...options, key }),
  );
  return key;
}

// Notistack's CommonJS build assigns these when its provider mounts, so they
// are read at call time, never captured at import.
export function enqueueSnackbar(messageOrOptions, options) {
  if (isDropped(messageOrOptions))
    return drop(
      (...args) => notistack.enqueueSnackbar(...args),
      messageOrOptions,
      options,
    );
  return notistack.enqueueSnackbar(messageOrOptions, options);
}

export function closeSnackbar(key) {
  return notistack.closeSnackbar(key);
}

export function useSnackbar() {
  const context = notistack.useSnackbar();
  // Stable for a stable context: callers list enqueueSnackbar in effect deps.
  return useMemo(
    () => ({
      ...context,
      enqueueSnackbar: (messageOrOptions, options) =>
        isDropped(messageOrOptions)
          ? drop(context.enqueueSnackbar, messageOrOptions, options)
          : context.enqueueSnackbar(messageOrOptions, options),
    }),
    [context],
  );
}
