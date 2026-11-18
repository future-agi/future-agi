import { create } from "src/utils/zustand";
import { createJSONStorage, devtools, persist } from "zustand/middleware";
import { CURRENT_ENVIRONMENT } from "src/config-global";
import { READ_MS, draftNameOf, mergeDesignFiles } from "./design";

// Designs waiting for approval, keyed by draft id. Stub: there's no design
// API yet, so a draft lives in this browser only, and the page reads it from
// here. Persisted so a refresh or a return later finds the same draft.
export const useDesignDraftStore = create(
  devtools(
    persist(
      (set, get, store) => ({
        drafts: {},

        // files: [{ id, name, size, type }]. Returns the new draft's id.
        createDesignDraft: ({
          name,
          description = "",
          files = [],
          personalChoice = null,
          agent = null,
          scenarioCount = null,
        }) => {
          const now = Date.now();
          const id = `design-${now.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
          const draft = {
            id,
            name: name?.trim() || draftNameOf(description, files),
            description: description.trim(),
            files,
            personalChoice: files.length ? personalChoice : null,
            agent,
            scenarioCount,
            createdAt: now,
            readUntil: files.length ? now + READ_MS : 0,
          };
          set(
            (s) => ({ drafts: { ...s.drafts, [id]: draft } }),
            false,
            "createDesignDraft",
          );
          return id;
        },

        // Files added while the design waits (from the page, later the chat):
        // merged by name and read again. Returns the merge note, or null for
        // an unknown draft.
        addFilesToDesign: (draftId, files) => {
          const draft = get().drafts[draftId];
          if (!draft) return null;
          const { files: next, note } = mergeDesignFiles(draft.files, files);
          if (note.added.length || note.replaced.length)
            set(
              (s) => ({
                drafts: {
                  ...s.drafts,
                  [draftId]: {
                    ...draft,
                    files: next,
                    readUntil: Date.now() + READ_MS,
                  },
                },
              }),
              false,
              "addFilesToDesign",
            );
          return note;
        },

        // A behaviour that can't be built: "simulate" or "skip".
        setDepChoice: (draftId, depId, choice) =>
          set(
            (s) =>
              s.drafts[draftId]
                ? {
                    drafts: {
                      ...s.drafts,
                      [draftId]: {
                        ...s.drafts[draftId],
                        depChoices: {
                          ...s.drafts[draftId].depChoices,
                          [depId]: choice,
                        },
                      },
                    },
                  }
                : {},
            false,
            "setDepChoice",
          ),

        discardDesignDraft: (draftId) =>
          set(
            (s) => {
              const { [draftId]: _gone, ...drafts } = s.drafts;
              return { drafts };
            },
            false,
            "discardDesignDraft",
          ),

        reset: () => set(store.getInitialState(), false, "reset"),
      }),
      {
        name: "simulate-design-drafts",
        storage: createJSONStorage(() => localStorage),
        partialize: (state) => ({ drafts: state.drafts }),
      },
    ),
    {
      name: "SimulateDesignDraftStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);
