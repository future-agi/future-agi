// Drawer-level state the picker shares with the New experiment drawer
// (lane Ra): which level is open (the drawer widens while the picker or a
// Level 3 form is open, and Level 3's sticky footer replaces the wizard
// footer) and the DOM node Level 3 portals its footer into.
//
// IT-R2 G1 P1: `layers` counts the panels open on top of the wizard (the
// trace drawer, the script pane) so Escape closes only the topmost one;
// `hold` is what an unsaved Level 3 draft would lose (`{name, simulated,
// tried}`), so closing the wizard asks first.
import { useEffect } from "react";
import { create } from "zustand";

export const usePickerUi = create((set) => ({
  level: 0, // 0 = no picker on screen, 1–3 = picker level
  footerEl: null,
  layers: 0,
  hold: null,
  // Bumps when a Level 3 button asks the wizard to run the experiment now
  // (`Add to experiment and open results`).
  submitRequest: 0,
  // options-truth L3 G8 P1 (entry:deep-link): the `entry=` of a deep link
  // whose card was already added. The key stays in the URL (a reload
  // reproduces the card) without adding the card again when the picker
  // remounts; the drawer clears it on close.
  deepEntry: null,
  setDeepEntry: (deepEntry) => set({ deepEntry }),
  setLevel: (level) => set({ level }),
  setFooterEl: (footerEl) => set({ footerEl }),
  pushLayer: () => set((s) => ({ layers: s.layers + 1 })),
  popLayer: () => set((s) => ({ layers: Math.max(0, s.layers - 1) })),
  setHold: (hold) => set({ hold }),
  requestSubmit: () => set((s) => ({ submitRequest: s.submitRequest + 1 })),
}));

/**
 * Registers a layer on top of the wizard while `open` is true: the wizard
 * then leaves Escape to that layer (its own listener closes it).
 */
export function useWizardLayer(open) {
  useEffect(() => {
    if (!open) return undefined;
    usePickerUi.getState().pushLayer();
    return () => usePickerUi.getState().popLayer();
  }, [open]);
}

/**
 * `Your LangGraph run and its simulated results will be lost.` (pure). The
 * names are the runs already added plus the Level 3 draft (`hold`).
 * @param {{names?: string[], simulated?: boolean, tried?: boolean}} p
 */
export function discardBody({ names = [], simulated = false, tried = false }) {
  const list = names.filter(Boolean);
  if (!list.length) return "Closing discards your changes.";
  const one = list.length === 1;
  const own = one ? "its" : "their";
  const tail = simulated
    ? ` and ${own} simulated results`
    : tried
      ? ` and ${own} 1-row try`
      : "";
  const what = one
    ? `Your ${list[0]} run`
    : `Your ${list.length} runs (${list.join(", ")})`;
  return `${what}${tail} will be lost.`;
}

/** `clamp(720px, 56vw, 960px)` while the picker is open (MUST 1). */
export const PICKER_DRAWER_WIDTH = "clamp(720px, 56vw, 960px)";
