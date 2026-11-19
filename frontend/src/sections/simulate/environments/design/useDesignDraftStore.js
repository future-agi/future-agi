import { create } from "src/utils/zustand";
import { createJSONStorage, devtools, persist } from "zustand/middleware";
import { CURRENT_ENVIRONMENT } from "src/config-global";
import { mergeFileRows } from "../buildDrawer/forms/shared/buildFiles";
import { READ_MS, untitledNameOf } from "./design";

// Drafts kept in this browser; creating one drops the oldest past this.
export const MAX_DESIGN_DRAFTS = 20;

// Designs waiting for approval, keyed by draft id. Stub: there's no design
// API yet, so a draft lives in this browser only, and the page reads it from
// here. Persisted so a refresh or a return later finds the same draft.
export const useDesignDraftStore = create(
  devtools(
    persist(
      (set, get, store) => ({
        drafts: {},

        // files: [{ id, name, size, type, documentId }], only ones that
        // uploaded (documentId from the upload). Each file keeps the personal data
        // choice it was added under. Returns the new draft's id.
        // takenNames: environment names an untitled draft mustn't repeat.
        createDesignDraft: ({
          name,
          description = "",
          files = [],
          personalChoice = null,
          agent = null,
          scenarioCount = null,
          takenNames = [],
        }) => {
          const now = Date.now();
          const id = `design-${now.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
          const typed = name?.trim();
          const draft = {
            id,
            name:
              typed ||
              untitledNameOf([
                ...takenNames,
                ...Object.values(get().drafts).map((d) => d.name),
              ]),
            // A typed name is never replaced by the builder's suggestion.
            nameTyped: !!typed,
            description: description.trim(),
            files: personalChoice
              ? files.map((f) => ({ ...f, personalChoice }))
              : files,
            agent,
            scenarioCount,
            createdAt: now,
            readUntil: files.length ? now + READ_MS : 0,
          };
          set(
            (s) => {
              // The new draft always stays, even on a createdAt tie.
              const kept = Object.values(s.drafts)
                .sort((a, b) => b.createdAt - a.createdAt)
                .slice(0, MAX_DESIGN_DRAFTS - 1);
              return {
                drafts: Object.fromEntries(
                  [...kept, draft].map((d) => [d.id, d]),
                ),
              };
            },
            false,
            "createDesignDraft",
          );
          return id;
        },

        // typed: false only when the builder's suggestion fills an untitled name.
        renameDraft: (draftId, name, { typed = true } = {}) =>
          set(
            (s) =>
              s.drafts[draftId]
                ? {
                    drafts: {
                      ...s.drafts,
                      [draftId]: {
                        ...s.drafts[draftId],
                        name,
                        nameTyped: typed,
                      },
                    },
                  }
                : {},
            false,
            "renameDraft",
          ),

        // Approval: the stub build runs from this moment (designBuildOf).
        startDesignBuild: (draftId) =>
          set(
            (s) =>
              s.drafts[draftId]
                ? {
                    drafts: {
                      ...s.drafts,
                      [draftId]: {
                        ...s.drafts[draftId],
                        build: { startedAt: Date.now() },
                      },
                    },
                  }
                : {},
            false,
            "startDesignBuild",
          ),

        // Save in Review changes: the checked build becomes v1. Only a build
        // that passed its checks can be saved, and only once.
        saveDesignRelease: (draftId, name = "") =>
          set(
            (s) =>
              s.drafts[draftId]?.build && !s.drafts[draftId].release
                ? {
                    drafts: {
                      ...s.drafts,
                      [draftId]: {
                        ...s.drafts[draftId],
                        release: { version: 1, name, savedAt: Date.now() },
                      },
                    },
                  }
                : {},
            false,
            "saveDesignRelease",
          ),

        // agent: { id, name, version } picked or connected after save. Kept
        // apart from `agent`, which an agent-built design was read from.
        connectDesignAgent: (draftId, agent) =>
          set(
            (s) =>
              s.drafts[draftId]
                ? {
                    drafts: {
                      ...s.drafts,
                      [draftId]: {
                        ...s.drafts[draftId],
                        connectedAgent: agent,
                      },
                    },
                  }
                : {},
            false,
            "connectDesignAgent",
          ),

        // A file left out to repair a failed build: the design is read again
        // without it and waits for approval again.
        leaveOutFile: (draftId, fileId) =>
          set(
            (s) =>
              s.drafts[draftId]
                ? {
                    drafts: {
                      ...s.drafts,
                      [draftId]: {
                        ...s.drafts[draftId],
                        files: s.drafts[draftId].files.filter(
                          (f) => f.id !== fileId,
                        ),
                        readUntil: Date.now() + READ_MS,
                        build: null,
                      },
                    },
                  }
                : {},
            false,
            "leaveOutFile",
          ),

        // Files added while the design waits (from the page, later the chat):
        // merged by name and read again. Returns the merge note, or null for
        // an unknown draft. Any edit drops a finished build, so the changed
        // design waits for approval again.
        addFilesToDesign: (draftId, files) => {
          const draft = get().drafts[draftId];
          if (!draft) return null;
          const { files: next, note } = mergeFileRows(draft.files, files);
          if (note.added.length || note.replaced.length)
            set(
              (s) => ({
                drafts: {
                  ...s.drafts,
                  [draftId]: {
                    ...draft,
                    files: next,
                    readUntil: Date.now() + READ_MS,
                    build: null,
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
                        build: null,
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
