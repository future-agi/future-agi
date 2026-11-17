// notify() — the one toast call.
// Toasts sit bottom-right of the page content (snackbar-provider),
// at most 2, 4 s (undo 6 s, never more than 6 s unless `persist`), deduped by `key`
// (a second `notify` with the same key REPLACES the first: `Snippet copied`
// never stacks). `undo` renders `Undo` (and listens for ⌘Z / Ctrl+Z while
// visible) and resolves the returned promise on undo or timeout.
//
//   const { undone } = notify("Row deleted", {
//     variant: "success", key: "row-delete",
//     undo: { onUndo: restore, ms: 6000 },
//   });
import React from "react";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { Button, Stack } from "@mui/material";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import { MOD_KEY_LABEL } from "src/utils/keymap";

export const UNDO_MS = 6000;
/** No toast outlives this unless it is `persist`. */
export const MAX_TOAST_MS = 6000;
const live = new Map(); // logical key → {snackKey, cleanup}
let seq = 0;

const isUndoKey = (e) =>
  (e.metaKey || e.ctrlKey) &&
  !e.shiftKey &&
  String(e.key).toLowerCase() === "z";

// No JSX: this is a .js module (Vite parses JSX only in .jsx).
const h = React.createElement;
function UndoAction({ onClick }) {
  return h(
    Stack,
    { direction: "row", alignItems: "center", gap: 0.5 },
    h(
      Button,
      {
        size: "small",
        color: "inherit",
        onClick,
        sx: { fontWeight: "fontWeightSemiBold", minWidth: 0, px: 1 },
      },
      "Undo",
    ),
    h(
      KeyCap,
      { sx: { height: 20, minWidth: 20, typography: "s3" } },
      `${MOD_KEY_LABEL}Z`,
    ),
  );
}

/**
 * Show a toast.
 * @param {React.ReactNode} message  sentence case (`Link copied`)
 * @param {{variant?: "default"|"success"|"info"|"warning"|"error",
 *   action?: (key) => React.ReactNode, key?: string,
 *   undo?: {onUndo: () => void, ms?: number, label?: string},
 *   autoHideDuration?: number, persist?: boolean,
 *   anchorOrigin?: {vertical: "top"|"bottom", horizontal: "left"|"center"|"right"}}} [opts]
 *   `anchorOrigin` is accepted and IGNORED: a top-center toast would sit on
 *   page and drawer headers. Every toast uses the one bottom-right column,
 *   which lifts over bottom bars (the review pager) and, next to an open
 *   drawer or dialog, moves left of it or shows over it, clear of its
 *   buttons; it is never hidden (toastPlacement.js).
 * @returns {{key: string, close: () => void, undone: Promise<boolean>}}
 *   `undone` resolves true when the user undid, false on timeout / close
 */
export function notify(message, opts = {}) {
  const {
    variant = "default",
    action,
    key: givenKey,
    undo,
    autoHideDuration,
    persist,
  } = opts;
  const key =
    givenKey ??
    (typeof message === "string" ? `t:${message}` : `t:${Date.now()}`);

  // Replace, never stack: close the live toast with this key first. Each
  // show gets its own snackbar key so an exiting toast never collides.
  if (live.has(key)) {
    const prev = live.get(key);
    prev.cleanup(false);
    closeSnackbar(prev.snackKey);
  }
  seq += 1;
  const snackKey = `${key}#${seq}`;

  let settle;
  const undone = new Promise((resolve) => {
    settle = resolve;
  });
  let done = false;
  const finish = (value) => {
    if (done) return;
    done = true;
    window.removeEventListener("keydown", onKey, true);
    if (live.get(key)?.snackKey === snackKey) live.delete(key);
    settle(value);
  };
  const doUndo = () => {
    if (done) return;
    try {
      undo?.onUndo?.();
    } finally {
      finish(true);
      closeSnackbar(snackKey);
    }
  };
  function onKey(e) {
    if (!undo || !isUndoKey(e)) return;
    const el = document.activeElement;
    const typing =
      el &&
      (el.tagName === "INPUT" ||
        el.tagName === "TEXTAREA" ||
        el.isContentEditable);
    if (typing) return;
    e.preventDefault();
    e.stopPropagation();
    doUndo();
  }
  if (undo) window.addEventListener("keydown", onKey, true);
  live.set(key, { snackKey, cleanup: finish });

  enqueueSnackbar(message, {
    key: snackKey,
    variant,
    preventDuplicate: false,
    autoHideDuration: undo
      ? Math.min(undo.ms ?? UNDO_MS, MAX_TOAST_MS)
      : Number.isFinite(autoHideDuration)
        ? Math.min(autoHideDuration, MAX_TOAST_MS)
        : autoHideDuration,
    persist,
    action: undo ? () => h(UndoAction, { onClick: doUndo }) : action,
    onExited: () => finish(false),
  });

  return { key, close: () => closeSnackbar(snackKey), undone };
}

export default notify;
