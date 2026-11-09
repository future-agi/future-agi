import { postProcessPopup } from "../common";

const guardedEditors = new WeakSet();

/** Cancel before AG Grid's popup-close listener can take the commit path. */
export function postProcessDatasetPopup(params) {
  if (params.type !== "popupCellEditor") {
    postProcessPopup(params);
    return;
  }
  const popup = params.ePopup;
  // AG can reposition the same popup and call this hook again. The listener
  // lives on the editor element, so it is collected when that popup is removed.
  if (guardedEditors.has(popup)) return;
  guardedEditors.add(popup);
  popup.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape") return;
      // AG Grid 35 defers its normal Escape cancel, but closes popups in the
      // same event. A close/blur may otherwise emit a real cell-value write.
      // Capture runs first, including for popups portalled to document.body.
      event.preventDefault();
      event.stopPropagation();
      params.api.stopEditing(true);
    },
    true,
  );
}
