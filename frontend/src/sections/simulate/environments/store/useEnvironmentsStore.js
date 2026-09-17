import { create } from "zustand";
import { createJSONStorage, devtools, persist } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";
import { emptyEnvState } from "./envState";

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

        // Adopted environment records, keyed by id (build/template/fork envs).
        workspaceEnvs: {},
        // Per-environment workspace state, keyed by id — the emptyEnvState shape.
        byEnv: {},

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

        // Register an environment. Prepend so the newest is first; idempotent by
        // id so a re-adopt (e.g. a stale remount at 7/7) never overwrites the
        // record or wipes its state. Seeds an empty state slice on first sight.
        adoptEnvironment: (env, now) =>
          set(
            (s) => {
              if (s.workspaceEnvs[env.id]) return {};
              return {
                workspaceEnvs: {
                  [env.id]: { ...env, adoptedAt: now },
                  ...s.workspaceEnvs,
                },
                byEnv: {
                  ...s.byEnv,
                  [env.id]: s.byEnv[env.id] || emptyEnvState(),
                },
              };
            },
            false,
            "adoptEnvironment",
          ),

        // Update fields on an already-adopted record (buildStatus, rename, …).
        // A no-op if the id was never adopted.
        patchEnvironment: (envId, patch) =>
          set(
            (s) => {
              if (!s.workspaceEnvs[envId]) return {};
              return {
                workspaceEnvs: {
                  ...s.workspaceEnvs,
                  [envId]: { ...s.workspaceEnvs[envId], ...patch },
                },
              };
            },
            false,
            "patchEnvironment",
          ),

        // Merge a patch into an env's state, defaulting to emptyEnvState() so a
        // never-seen env still lands on the full shape.
        patchEnvState: (envId, patch) =>
          set(
            (s) => ({
              byEnv: {
                ...s.byEnv,
                [envId]: { ...(s.byEnv[envId] || emptyEnvState()), ...patch },
              },
            }),
            false,
            "patchEnvState",
          ),

        // Upsert a run by id, keeping its position. A run is recorded on start
        // and again on completion; the second call merges into the same row so
        // the list never grows a duplicate or reorders.
        recordRun: (envId, run) =>
          set(
            (s) => {
              const prev = s.byEnv[envId] || emptyEnvState();
              const runs = prev.runs || [];
              const idx = runs.findIndex((r) => r.id === run.id);
              const nextRuns =
                idx >= 0
                  ? runs.map((r, i) => (i === idx ? { ...r, ...run } : r))
                  : [run, ...runs];
              return {
                byEnv: { ...s.byEnv, [envId]: { ...prev, runs: nextRuns } },
              };
            },
            false,
            "recordRun",
          ),

        addAgentVersion: (envId, version) =>
          set(
            (s) => {
              const prev = s.byEnv[envId] || emptyEnvState();
              return {
                byEnv: {
                  ...s.byEnv,
                  [envId]: {
                    ...prev,
                    agentVersions: [...(prev.agentVersions || []), version],
                  },
                },
              };
            },
            false,
            "addAgentVersion",
          ),

        // Register a fork produced by helpers/forkEnvironment.js. `fork` is the
        // pure `{ env, envState }` clone; the source id is accepted for call-site
        // symmetry. Prepends the fork record and writes its cloned state.
        forkEnvironment: (envId, fork) =>
          set(
            (s) => ({
              workspaceEnvs: { [fork.env.id]: fork.env, ...s.workspaceEnvs },
              byEnv: { ...s.byEnv, [fork.env.id]: fork.envState },
            }),
            false,
            "forkEnvironment",
          ),

        // Clear only the entry + build slice. The env slices (byEnv,
        // workspaceEnvs) survive, so visiting Home never wipes a workspace.
        resetEntryState: () =>
          set(
            {
              choice: null,
              draft: null,
              buildStage: null,
              envId: null,
              readerAnswers: null,
              retriedSections: [],
              buildProgress: { ...INITIAL_BUILD_PROGRESS },
            },
            false,
            "resetEntryState",
          ),

        reset: () => set(store.getInitialState(), false, "reset"),
      }),
      {
        name: "simulate-environments-draft",
        storage: createJSONStorage(() => sessionStorage),
        // The draft and the env slices survive a refresh so a workspace deep
        // link (or the in-place workspace on /build) can rehydrate. The build
        // slice must NOT persist, or the reset-on-mount / startPreflight
        // semantics would break (decision 5).
        partialize: (state) => ({
          draft: state.draft,
          byEnv: state.byEnv,
          workspaceEnvs: state.workspaceEnvs,
        }),
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

// Home mounts call this instead of the full reset so the entry matrix starts
// clean without discarding the user's adopted workspaces.
export const resetEnvironmentsEntryState = () =>
  useEnvironmentsStore.getState().resetEntryState();
