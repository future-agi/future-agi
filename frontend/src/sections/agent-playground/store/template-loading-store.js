import { create } from "src/utils/zustand";
import { devtools } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";

export const useTemplateLoadingStore = create(
  devtools(
    (set, get, store) => ({
      // Loading state
      isLoadingTemplate: false,
      loadingProgress: 0,
      loadingMessage: "",
      loadingTemplateId: null,
      loadingTemplateName: "",

      // Abort controller for cancellation
      abortController: null,

      // Stop confirmation dialog
      showStopConfirmDialog: false,

      // Actions
      startLoadingTemplate: (templateId, templateName) => {
        const controller = new AbortController();
        set(
          {
            isLoadingTemplate: true,
            loadingProgress: 0,
            loadingMessage: "Preparing template...",
            loadingTemplateId: templateId,
            loadingTemplateName: templateName,
            abortController: controller,
            showStopConfirmDialog: false,
          },
          false,
          "startLoadingTemplate",
        );
        return controller;
      },

      updateLoadingProgress: (progress, message) =>
        set(
          { loadingProgress: progress, loadingMessage: message },
          false,
          "updateLoadingProgress",
        ),

      completeLoadingTemplate: () =>
        set(
          {
            isLoadingTemplate: false,
            loadingProgress: 100,
            loadingMessage: "Complete!",
            loadingTemplateId: null,
            loadingTemplateName: "",
            abortController: null,
            showStopConfirmDialog: false,
          },
          false,
          "completeLoadingTemplate",
        ),

      cancelLoadingTemplate: () => {
        const { abortController } = get();
        if (abortController) {
          abortController.abort();
        }
        set(
          {
            isLoadingTemplate: false,
            loadingProgress: 0,
            loadingMessage: "",
            loadingTemplateId: null,
            loadingTemplateName: "",
            abortController: null,
            showStopConfirmDialog: false,
          },
          false,
          "cancelLoadingTemplate",
        );
      },

      setShowStopConfirmDialog: (show) =>
        set({ showStopConfirmDialog: show }, false, "setShowStopConfirmDialog"),

      reset: () => {
        const { abortController } = get();
        if (abortController) {
          abortController.abort();
        }
        set(store.getInitialState(), false, "reset");
      },
    }),
    {
      name: "TemplateLoadingStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const useTemplateLoadingStoreShallow = (fun) =>
  useTemplateLoadingStore(useShallow(fun));

export const resetTemplateLoadingStore = () => {
  useTemplateLoadingStore.getState().reset();
};
