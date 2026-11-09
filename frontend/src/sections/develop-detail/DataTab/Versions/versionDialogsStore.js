// Which Versions dialog is open (PRODUCT-SPEC §3.5). One host per dataset
// page (`<PendingChangesGate/>` in DevelopDetailView) renders the Save and
// interstitial dialogs, so the bar, the gate and other lanes' actions share
// them. Not part of resetAllStates(): a dialog never outlives a route change
// because the host unmounts with the page.
import { create } from "zustand";

export const useVersionDialogs = create((set) => ({
  /** `{datasetId, onSaved?}` while Save version is open. */
  save: null,
  /** `{datasetId, kind: "structural" | "experiment", proceed}` while the
   * pending-changes interstitial is open. */
  gate: null,
  openSave: (datasetId, { onSaved } = {}) =>
    set({ save: { datasetId, onSaved } }),
  closeSave: () => set({ save: null }),
  openGate: (datasetId, kind, proceed) =>
    set({ gate: { datasetId, kind, proceed } }),
  closeGate: () => set({ gate: null }),
}));
