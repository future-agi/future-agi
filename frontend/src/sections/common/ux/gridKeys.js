// Keyboard-first grids (R4-SPEC D25; frozen API).
//
//   suppressGridTyping({event, editing}) → bool   AG Grid `suppressKeyboardEvent`:
//     while a cell is not being edited, every printable key, `Enter` and
//     `Space` is a shortcut (useShortcuts / the `?` sheet), never "start
//     typing". Editing starts with F2, a double-click or the row's Edit
//     action. Modifier chords (⌘C, Ctrl+V) and arrows / Tab / F2 / Escape
//     stay with the grid.
//   useRowCursor(gridApiRef, {surface, enabled}) — iterate r3 G3 (P4): no
//     cursor at load (no ring or checkbox on row 1); the first J / K / ↓ /
//     ↑ / Enter / X shows it on row 1 (that key only shows it). Focus
//     returns to the last row after `?`, a dialog or a toast closes, and
//     after a mouse click on a chip / toggle / checkbox outside the grid
//     (so Enter opens the row, not the chip again).
//
//   <AgGridReact defaultColDef={{ ..., suppressKeyboardEvent: suppressGridTyping }} />
//   useRowCursor(gridRef, { surface: "grid" });
import { useEffect, useRef } from "react";
import { useShortcutStore } from "./useShortcuts";

/**
 * @param {{event?: KeyboardEvent, editing?: boolean}} params  AG Grid's
 *   SuppressKeyboardEventParams
 * @returns {boolean} true = the grid ignores the key
 */
export function suppressGridTyping(params) {
  const event = params?.event;
  if (!event || params?.editing) return false;
  if (event.metaKey || event.ctrlKey || event.altKey) return false;
  const key = event.key;
  if (key === "Enter" || key === " " || key === "Spacebar") return true;
  return typeof key === "string" && key.length === 1;
}

/** Keys that show the row cursor the first time (P4). */
const FIRST_KEYS = new Set([
  "j",
  "J",
  "k",
  "K",
  "ArrowDown",
  "ArrowUp",
  "Enter",
  "x",
  "X",
  "Home",
  "End",
  "PageDown",
  "PageUp",
]);

const OVERLAY_SELECTOR =
  ".MuiDialog-root, .MuiPopover-root, .MuiMenu-root, .MuiDrawer-modal, [data-fa-shortcut-sheet], .notistack-SnackbarContainer, .MuiSnackbar-root";

const bodyHasFocus = () => {
  if (typeof document === "undefined") return false;
  const el = document.activeElement;
  return !el || el === document.body || el === document.documentElement;
};
const overlayOpen = () =>
  typeof document !== "undefined" &&
  Boolean(
    document.querySelector(
      ".MuiDialog-root, .MuiPopover-root, .MuiMenu-root, [data-fa-shortcut-sheet]",
    ),
  );

const apiOf = (ref) => {
  const cur = ref?.current;
  if (!cur) return null;
  const api = cur.api ?? cur;
  if (typeof api?.setFocusedCell !== "function") return null;
  if (typeof api.isDestroyed === "function" && api.isDestroyed()) return null;
  return api;
};

/**
 * Give a grid a row cursor on load and after overlays close.
 * @param {{current: object}} gridApiRef  ref to an AgGridReact (`.api`) or
 *   to the grid api itself
 * @param {{surface?: string, enabled?: boolean}} [opts]  the cursor is
 *   only restored while `surface` is the current shortcut surface (or none)
 */
export function useRowCursor(gridApiRef, { surface, enabled = true } = {}) {
  const last = useRef(null);
  useEffect(() => {
    if (!enabled || typeof document === "undefined") return undefined;
    let disposed = false;
    const mine = () => {
      const cur = useShortcutStore.getState().current();
      return !surface || !cur || cur === surface;
    };
    const focusCursor = () => {
      const api = apiOf(gridApiRef);
      if (disposed || !api || !bodyHasFocus() || overlayOpen() || !mine())
        return false;
      const cols = api.getAllDisplayedColumns?.() ?? [];
      const rows = api.getDisplayedRowCount?.() ?? 0;
      if (!cols.length || !rows) return false;
      const rowIndex = Math.min(last.current?.rowIndex ?? 0, rows - 1);
      const column = last.current?.column ?? cols[0];
      api.ensureIndexVisible?.(rowIndex);
      api.setFocusedCell(rowIndex, column);
      return true;
    };
    // iterate r3 G3 (P4): no cursor at load; the first navigation key
    // shows it (and does nothing else), so row 1 is never ringed unasked.
    let shown = false;
    const onFirstKey = (e) => {
      if (shown || e.defaultPrevented) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!FIRST_KEYS.has(e.key)) return;
      if (!bodyHasFocus()) return;
      if (focusCursor()) {
        shown = true;
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    };
    document.addEventListener("keydown", onFirstKey, true);
    // A mouse click on a chip / toggle / checkbox outside the grid leaves
    // focus there (Enter would press it again): back to the row cursor, or
    // to the page when no cursor was shown yet.
    const onClick = (e) => {
      if (!e.detail || !mine()) return;
      const el = e.target?.closest?.(
        'button, [role="button"], input[type="checkbox"], input[type="radio"], .MuiChip-root',
      );
      if (!el || el.closest?.(".ag-root-wrapper")) return;
      if (el.getAttribute?.("aria-haspopup") || el.closest?.(OVERLAY_SELECTOR))
        return;
      setTimeout(() => {
        if (disposed || overlayOpen()) return;
        const active = document.activeElement;
        if (!active || !(active === el || el.contains(active))) return;
        const api = apiOf(gridApiRef);
        if (shown && last.current && api) {
          api.setFocusedCell(last.current.rowIndex, last.current.column);
        } else active.blur?.();
      }, 0);
    };
    document.addEventListener("click", onClick, true);
    const timer = null;
    // Remember the cursor.
    let listening = null;
    const onFocused = (e) => {
      if (e?.rowIndex != null && e?.column) {
        shown = true;
        last.current = { rowIndex: e.rowIndex, column: e.column };
      }
    };
    const attach = setInterval(() => {
      const api = apiOf(gridApiRef);
      if (!api || listening) return;
      api.addEventListener?.("cellFocused", onFocused);
      listening = api;
      clearInterval(attach);
    }, 250);
    // Overlay closed with focus lost inside it → back to the row.
    const onFocusOut = (e) => {
      if (e.relatedTarget || !shown) return;
      if (!e.target?.closest?.(OVERLAY_SELECTOR)) return;
      setTimeout(focusCursor, 80);
    };
    document.addEventListener("focusout", onFocusOut, true);
    // The `?` sheet closed.
    const unsub = useShortcutStore.subscribe((s, prev) => {
      if (shown && prev?.sheet?.open && !s.sheet.open)
        setTimeout(focusCursor, 80);
    });
    return () => {
      disposed = true;
      clearInterval(timer);
      document.removeEventListener("keydown", onFirstKey, true);
      document.removeEventListener("click", onClick, true);
      clearInterval(attach);
      document.removeEventListener("focusout", onFocusOut, true);
      unsub();
      if (listening && !listening.isDestroyed?.())
        listening.removeEventListener?.("cellFocused", onFocused);
    };
  }, [gridApiRef, surface, enabled]);
}
