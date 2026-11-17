import { useEffect, useRef } from "react";
import {
  hasCommandModifier,
  isInGrid,
  isSafeForSingleKey,
  isTextInput,
} from "src/utils/keyboard-guards";
import { actionForEvent, isArrowKey, keymapEntry } from "src/utils/keymap";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";

/** True while an MUI modal, menu or popover is open over the page. */
const overlayOpen = () =>
  typeof document !== "undefined" &&
  !!document.querySelector(".MuiModal-root:not(.MuiModal-hidden)");

/**
 * iterate r3 G3 merge: ↓ / ↑ are next / previous now (keymap.js), so they
 * stay with an element that uses vertical arrows itself: a select, slider,
 * spinbutton, open menu or listbox (isSafeForSingleKey), a radio, a tree or
 * a non-AG grid.
 */
const ownsVerticalArrows = (el) =>
  !isSafeForSingleKey(el) ||
  (el?.tagName?.toLowerCase() === "input" &&
    String(el.type).toLowerCase() === "radio") ||
  !!el?.closest?.(
    '[role="radio"], [role="radiogroup"], [role="tree"], [role="treegrid"], [role="grid"]',
  );

/**
 * Resolve a keydown to a handler from `keys` (by KEYMAP action name or by
 * the literal `event.key`) or the named callbacks. Exported for tests.
 */
export function resolveRowKey(
  event,
  { onNext, onPrev, onClose, onFull, keys = {}, surface = "row" },
) {
  const action = actionForEvent(event, surface);
  if (action && keys[action]) return keys[action];
  const literal = keys[event.key] ?? keys[event.key?.toLowerCase?.()];
  if (literal && !hasCommandModifier(event)) return literal;
  switch (action) {
    case "next":
      return onNext;
    case "prev":
      return onPrev;
    case "escape":
      return onClose;
    case "full":
      return onFull;
    default:
      return null;
  }
}

/**
 * useRowKeyboardNav — the §4.1 keymap for a row surface (Row view, result
 * drawer). Working minimal version (L0; owned by L1).
 *
 * - `J`/`K` (and `↓`/`↑`, iterate r3 G3; `→`/`←` no longer) call onNext /
 *   onPrev. Arrows never fire while the grid or a text field has focus
 *   (`gridFocused` forces that off), nor from an element that uses vertical
 *   arrows itself (select, slider, menu, listbox, radio, tree); single keys
 *   also work from a focused grid cell, never from a text field.
 * - `Esc` calls onClose (the caller steps full → split → closed); `F` onFull.
 * - `keys` adds handlers by action name (`diff`, `raw`, `edit`, `copyLink`,
 *   `addToQueue`, `help`, `submit`, R3: `tab` (1–4), `splits`, `collapseAll`,
 *   `copyRowId` (⇧C), `saveVersion` (⌘S), …) or by literal key.
 * - R3 (RV#1): single keys keep working after a click on a tab, chip or
 *   button (keyboard-guards treats them as safe).
 * - `⌘↵` (`submit`) fires even from a text field, like the workspace.
 * - Nothing fires while a dialog or menu is open, or when `enabled` is false.
 *
 * Props (frozen):
 * @param {{enabled?: boolean, onNext?: Function, onPrev?: Function,
 *   onClose?: Function, onFull?: Function, keys?: Object<string, Function>,
 *   gridFocused?: boolean, surface?: "row"|"drawer"}} opts
 */
export default function useRowKeyboardNav(opts) {
  const ref = useRef(opts);
  ref.current = opts;
  const enabled = opts?.enabled !== false;
  const surface = opts?.surface ?? "row";

  // R3: the open panel is the `?` sheet's current surface.
  useEffect(() => {
    if (!enabled) return undefined;
    const token = useShortcutStore.getState().push(surface);
    return () => useShortcutStore.getState().pop(token);
  }, [enabled, surface]);

  useEffect(() => {
    if (!enabled) return undefined;
    const handler = (event) => {
      if (event.defaultPrevented || event.isComposing) return;
      const o = ref.current ?? {};
      const focused = document.activeElement;
      const isSubmit =
        (event.metaKey || event.ctrlKey) &&
        keymapEntry("submit").keys.includes(`mod+${event.key}`);

      if (isSubmit) {
        // R3: on the Row view Cmd+S saves a version, unless a field editor
        // has focus (then it applies the edit, as before).
        const action = actionForEvent(event, o.surface ?? "row");
        const fn =
          action === "saveVersion" &&
          o.keys?.saveVersion &&
          !isTextInput(focused)
            ? o.keys.saveVersion
            : o.keys?.submit;
        if (fn) {
          event.preventDefault();
          fn(event);
        }
        return;
      }
      if (overlayOpen()) return;
      if (isTextInput(focused)) return;
      if (isArrowKey(event)) {
        if (o.gridFocused || isInGrid(focused)) return;
        if (
          (event.key === "ArrowUp" || event.key === "ArrowDown") &&
          ownsVerticalArrows(focused)
        )
          return;
      } else if (event.key !== "Escape") {
        if (hasCommandModifier(event)) return;
        if (!isSafeForSingleKey(focused, { allowGrid: true })) return;
      }
      const fn = resolveRowKey(event, o);
      if (!fn) return;
      event.preventDefault();
      fn(event);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [enabled]);
}
