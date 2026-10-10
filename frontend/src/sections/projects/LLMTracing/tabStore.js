import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export const useTabStore = create((set) => ({
  // Unsaved filter/column changes on current view
  dirtyConfig: null,
  isDirty: false,

  // Context menu
  contextMenuAnchor: null, // { x, y, viewId } | null

  // Create/edit modal
  createModalOpen: false,
  editModalView: null, // view object when editing, null when creating

  saveAsNewRequested: false,
  requestSaveAsNew: () => set({ saveAsNewRequested: true }),
  consumeSaveAsNew: () => set({ saveAsNewRequested: false }),

  // Inline rename
  editingTabId: null,

  // Actions
  setDirtyConfig: (config) =>
    set({ dirtyConfig: config, isDirty: config !== null }),
  clearDirty: () => set({ dirtyConfig: null, isDirty: false }),

  openContextMenu: (x, y, viewId) =>
    set({ contextMenuAnchor: { x, y, viewId } }),
  closeContextMenu: () => set({ contextMenuAnchor: null }),

  openCreateModal: () => set({ createModalOpen: true, editModalView: null }),
  openEditModal: (view) => set({ createModalOpen: true, editModalView: view }),
  closeCreateModal: () => set({ createModalOpen: false, editModalView: null }),

  startRenaming: (viewId) => set({ editingTabId: viewId }),
  stopRenaming: () => set({ editingTabId: null }),
}));

export const useTabStoreShallow = (fn) => useTabStore(useShallow(fn));

export const resetTabStore = () => {
  useTabStore.setState({
    saveAsNewRequested: false,
    dirtyConfig: null,
    isDirty: false,
    contextMenuAnchor: null,
    createModalOpen: false,
    editModalView: null,
    editingTabId: null,
  });
};
