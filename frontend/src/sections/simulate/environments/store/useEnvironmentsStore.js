import { create } from "zustand";
import { createJSONStorage, devtools, persist } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";

// The client build-stage enum lives here (re-exported by build/build.constants.js)
// so the store and the build modules share one source with no import ordering.
export const BUILD_STAGE = { PREFLIGHT: "preflight", BUILDING: "building" };

// Read-audit section keys, in the designer's READ_SECTIONS order. Hardcoded (not
// imported from build/readAudit.constants) to keep the store free of UI coupling.
const SECTION_KEYS = ["tools", "rules", "data", "behavior"];

const INITIAL_BUILD_PROGRESS = { done: [], running: false, failure: null };

export const useEnvironmentsStore = create(
  devtools(
    persist(
      (set, get, store) => ({
        choice: null, // OPTION_ID of the open entry card | null
        draft: null, // source object a panel CTA produced | null

        buildStage: null, // BUILD_STAGE | null (null = not on the build page)
        envId: null, // minted by useBuildEnvironment when the audit is accepted
        readerAnswers: null, // { [questionId]: { pick, other, skipped } } | null
        retriedSections: [], // section keys the user hit "Retry read" on
        buildProgress: { ...INITIAL_BUILD_PROGRESS },

        setChoice: (id) => set({ choice: id }, false, "setChoice"),
        clearChoice: () => set({ choice: null }, false, "clearChoice"),
        setDraft: (source) => set({ draft: source }, false, "setDraft"),
        clearDraft: () => set({ draft: null }, false, "clearDraft"),

        // A fresh visit to /build must never inherit a stale building state, so
        // the whole build slice resets here — only draft (persisted) survives.
        startPreflight: () =>
          set(
            {
              buildStage: BUILD_STAGE.PREFLIGHT,
              envId: null,
              readerAnswers: null,
              retriedSections: [],
              buildProgress: { ...INITIAL_BUILD_PROGRESS },
            },
            false,
            "startPreflight",
          ),

        acceptAudit: ({ envId, answers }) =>
          set(
            {
              buildStage: BUILD_STAGE.BUILDING,
              envId,
              readerAnswers: answers,
            },
            false,
            "acceptAudit",
          ),

        retrySection: (key) =>
          set(
            (s) =>
              s.retriedSections.includes(key)
                ? {}
                : { retriedSections: [...s.retriedSections, key] },
            false,
            "retrySection",
          ),

        retryAll: () =>
          set({ retriedSections: [...SECTION_KEYS] }, false, "retryAll"),

        setBuildProgress: (patch) =>
          set(
            (s) => ({ buildProgress: { ...s.buildProgress, ...patch } }),
            false,
            "setBuildProgress",
          ),

        reset: () => set(store.getInitialState(), false, "reset"),
      }),
      {
        name: "simulate-environments-draft",
        storage: createJSONStorage(() => sessionStorage),
        // Only the draft survives a refresh: the build slice must not, or the
        // reset-on-mount / startPreflight semantics would break (decision 5).
        partialize: (state) => ({ draft: state.draft }),
      },
    ),
    {
      name: "SimulateEnvironmentsStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const useEnvironmentsStoreShallow = (fn) =>
  useEnvironmentsStore(useShallow(fn));

export const resetEnvironmentsStore = () =>
  useEnvironmentsStore.getState().reset();
