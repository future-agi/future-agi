import { create } from "src/utils/zustand";
import { devtools } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";

export const VIEW = {
  ACTIONS: "actions",
  MANUAL_FORM: "manual_form",
  UPLOADED_JSON: "uploaded_json",
};

export const useGlobalVariablesDrawerStore = create(
  devtools(
    (set, get, store) => ({
      open: false,
      setOpen: (open) => set({ open }),
      pendingRun: false,
      setPendingRun: (pending) => set({ pendingRun: pending }),
      currentView: VIEW.MANUAL_FORM,
      setCurrentView: (view) => set({ currentView: view }),

      // Import Dataset Drawer state
      importDatasetDrawerOpen: false,
      setImportDatasetDrawerOpen: (open) =>
        set(
          { importDatasetDrawerOpen: open },
          false,
          "setImportDatasetDrawerOpen",
        ),

      globalVariables: {},
      uploadedJson: null,
      uploadedFileName: null,
      setUploadedJson: (json, fileName = null) =>
        set({ uploadedJson: json, uploadedFileName: fileName }),

      updateGlobalVariables: (key, value) => {
        set(
          { globalVariables: { ...get().globalVariables, [key]: value } },
          false,
          "updateGlobalVariables",
        );
      },

      setGlobalVariables: (values) => {
        set({ globalVariables: { ...values } }, false, "setGlobalVariables");
      },

      addGlobalVariableKey: (key) => {
        const { globalVariables } = get();
        set(
          { globalVariables: { ...globalVariables, [key]: "" } },
          false,
          "addGlobalVariableKey",
        );
      },

      deleteGlobalVariables: (key) => {
        const { globalVariables } = get();
        const next = { ...globalVariables };
        delete next[key];
        set({ globalVariables: next }, false, "deleteGlobalVariables");
      },

      reset: () => {
        set(store.getInitialState(), false, "reset");
      },
    }),
    {
      name: "GlobalVariablesDrawerStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const resetGlobalVariablesDrawerStore = () => {
  useGlobalVariablesDrawerStore.getState().reset();
};

export const useGlobalVariablesDrawerStoreShallow = (fun) =>
  useGlobalVariablesDrawerStore(useShallow(fun));
