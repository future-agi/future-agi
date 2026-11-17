// Focus guards for single-key shortcuts. Moved from
// sections/annotations/queues/annotate/use-keyboard-shortcuts.js (which now
// imports them) so the Row view, the result drawer and the review workspace
// share one rule (PRODUCT-SPEC §4.1).
//
// R3 (G0, RV#1): focus on a button, tab, chip, link, menu trigger or grid
// cell is SAFE — after a mouse click on a tab, `J` still moves the row.
// Only places that take typing or own their keys block: text inputs,
// textareas, contentEditable, selects, sliders / spinbuttons, open menus and
// listboxes, and dialogs (except the `?` shortcut sheet).

// iterate r3 G3 (P4): checkboxes, radios and button-like inputs take no
// text — after clicking a row checkbox, `J` / `X` / Enter still work.
const NON_TEXT_INPUTS = new Set([
  "checkbox",
  "radio",
  "button",
  "submit",
  "reset",
  "image",
  "color",
  "file",
]);

/** True when `el` takes typed text: input, textarea or a contentEditable. */
export const isTextInput = (el) => {
  const tag = el?.tagName?.toLowerCase();
  if (tag === "input")
    return !NON_TEXT_INPUTS.has(String(el.type ?? "").toLowerCase());
  return tag === "textarea" || !!el?.isContentEditable;
};

/** True when `el` sits inside an AG Grid (cells, headers, the viewport). */
export const isInGrid = (el) =>
  !!el?.closest?.(".ag-root-wrapper, .ag-root, .ag-theme-quartz, .ag-theme");

/** Roles that own their keys (typing, arrows, menus). */
const BLOCKING_ROLES = new Set([
  "textbox",
  "searchbox",
  "combobox",
  "listbox",
  "option",
  "menu",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "slider",
  "spinbutton",
]);

/** Marker on the shortcut sheet's dialog: its focus never blocks keys. */
export const SHORTCUT_SHEET_ATTR = "data-fa-shortcut-sheet";

/** True when `el` is inside a dialog other than the shortcut sheet. */
export const isInBlockingDialog = (el) => {
  const dialog = el?.closest?.('[role="dialog"], [role="alertdialog"]');
  if (!dialog) return false;
  return !dialog.closest?.(`[${SHORTCUT_SHEET_ATTR}]`);
};

/**
 * True when the focused element is safe for single-key shortcuts.
 * Safe: body, buttons, links, tabs, chips, menu triggers, grid cells and
 * plain elements. Blocking: text fields, selects, sliders, open menus /
 * listboxes, and dialogs other than the shortcut sheet.
 *
 * `allowGrid` is kept for callers; grid cells are always safe now (their
 * editors are text inputs and still block). Arrow keys stay the grid's:
 * callers check `isInGrid` before acting on arrows.
 *
 * @param {Element|null} el
 * @param {{allowGrid?: boolean}} [opts]
 */
// eslint-disable-next-line no-unused-vars
export const isSafeForSingleKey = (el, { allowGrid = false } = {}) => {
  if (!el || (typeof document !== "undefined" && el === document.body))
    return true;
  if (isTextInput(el)) return false;
  const tag = el.tagName?.toLowerCase();
  if (tag === "select") return false;
  const role = el.getAttribute?.("role");
  if (role && BLOCKING_ROLES.has(role)) return false;
  if (el.closest?.('[role="menu"], [role="listbox"]')) return false;
  if (isInBlockingDialog(el)) return false;
  return true;
};

/** True for elements a click or Enter/Space activates (buttons, links, tabs). */
export const isActivatable = (el) => {
  if (!el) return false;
  const tag = el.tagName?.toLowerCase();
  if (tag === "button" || tag === "a" || tag === "summary") return true;
  const role = el.getAttribute?.("role");
  return (
    role === "button" ||
    role === "tab" ||
    role === "link" ||
    role === "checkbox" ||
    role === "switch" ||
    role === "radio"
  );
};

/** True when a modifier other than Shift is held (Cmd, Ctrl or Alt). */
export const hasCommandModifier = (event) =>
  !!(event?.metaKey || event?.ctrlKey || event?.altKey);
