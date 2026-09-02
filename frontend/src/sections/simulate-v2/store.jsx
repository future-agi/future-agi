/**
 * Prototype-local state for the simulation flow.
 *
 * Deliberately not react-query / not the API layer: this whole section is a
 * frontend prototype for stakeholder review, so the "backend" is this reducer
 * plus localStorage. Swapping it for real endpoints later means replacing this
 * one file's actions, not the screens.
 */
import PropTypes from "prop-types";
import {
  createContext,
  useContext,
  useMemo,
  useReducer,
  useEffect,
  useCallback,
  useRef,
} from "react";
import { seededState } from "./_mock/seedState";

/*
  Versioned: the seeded first-run environments only reach anyone whose browser
  already holds an older (empty) cache if the key changes with them. Bump this
  whenever the seeded state or the state shape changes.
*/
const STORAGE_KEY = "fagi.simulate.studio.v16";

const emptyEnvState = () => ({
  agent: null, // source agent { typeId, values, connectedAt } — this env's contract was read from
  /*
    Additional agents the user has attached for testing. Each has the
    same shape as `agent` plus an `id` and an optional `note`. The
    source agent stays in `agent`; adding another does NOT replace the
    source. Users can promote an additional agent to "source" (which
    re-derives the env) via a confirmation dialog.
  */
  additionalAgents: [], // Array<{ id, typeId, values, via, connectedAt, note }>
  /*
    Which agent the next simulation run will target. `null` (the
    default) means the source agent; otherwise the id of one of
    `additionalAgents`. Runs stamp whichever agent was active on
    themselves so run history stays attributable.
  */
  activeAgentId: null,
  scenarios: [], // scenario rows chosen or generated
  scenarioSource: null, // 'templates' | 'chat' | 'agent'
  evals: [], // eval ids
  runs: [], // completed run summaries
  /* Versions of the agent under test, oldest first. Minted when the agent
     changes, never by running — see _mock/versions.js. */
  agentVersions: [],
  /* Versions of the world. Empty until someone changes it; the seeded history
     stands in until then. */
  envVersions: [],
  /*
    What was actually shipped.

    The gate decides whether a version *may* ship and then the decision
    evaporates — so "how does this compare to what is live" cannot be asked,
    and the baseline gets re-picked from memory every time. A release is one
    line: which version, off which run, when, and whether the gate was clear or
    overridden.
  */
  releases: [],
  /*
    Twin backing — when the environment's world is a live sandbox for
    third-party services (Slack, Notion, Salesforce, etc.) instead of
    the default seeded generic tables. `null` when this env has no
    twin backing; otherwise the sandbox we provisioned for it.
    Shape: { services, seedPrompt, seed, endpoints, activity, provisionedAt }.
    Scenarios can override the `seed` per-scenario without touching
    the env-level default (follow-up work).
  */
  twinBacking: null,
});

const initialState = {
  myEnvironments: [], // environments the user created / adopted
  byEnv: {}, // envId -> emptyEnvState()
  activeRun: null,
};

/*
  A first visit lands on an empty "My environments", which hides the workspace,
  runs and eval mapping behind setup nobody has done yet. Seed a few that look
  like work already in progress — and only when nothing is stored, so it never
  overwrites what someone actually did.
*/
const firstRunState = () => ({ ...initialState, ...seededState(), seedVersion: SEED_VERSION });

/*
  Bumped whenever the seed gains an environment.

  Bumping the storage key instead would work, and would also throw away every
  run the person looking at this has made since — which is the state that makes
  the prototype worth showing. So a stored state is topped up rather than
  replaced: environments it has never seen are added, environments it already
  has are left exactly as they are, and an environment someone deliberately
  removed stays removed because the marker moves on regardless.
*/
const SEED_VERSION = 2;

const topUpSeed = (stored) => {
  if ((stored.seedVersion || 0) >= SEED_VERSION) return { ...stored, seedVersion: SEED_VERSION };
  const seed = seededState();
  const have = new Set((stored.myEnvironments || []).map((e) => e.id));
  const missing = seed.myEnvironments.filter((e) => !have.has(e.id));
  if (!missing.length) return { ...stored, seedVersion: SEED_VERSION };

  const byEnv = { ...stored.byEnv };
  missing.forEach((e) => { byEnv[e.id] = seed.byEnv[e.id]; });
  return {
    ...stored,
    seedVersion: SEED_VERSION,
    myEnvironments: [...(stored.myEnvironments || []), ...missing],
    byEnv,
  };
};

function reducer(state, action) {
  switch (action.type) {
    case "hydrate":
      return { ...state, ...action.payload };

    case "adoptEnvironment": {
      const { env } = action;
      if (state.myEnvironments.some((e) => e.id === env.id)) return state;
      return {
        ...state,
        myEnvironments: [
          { ...env, adoptedAt: action.now, custom: !!env.custom },
          ...state.myEnvironments,
        ],
        byEnv: { ...state.byEnv, [env.id]: state.byEnv[env.id] || emptyEnvState() },
      };
    }

    /*
      Update fields on an already-adopted environment — the buildStatus flag
      that tracks how far derivation has got, a rename, a difficulty change.
      A no-op if the id is not in myEnvironments; adoption is a separate
      action on purpose.
    */
    case "patchEnvironment": {
      const { envId, patch } = action;
      const idx = state.myEnvironments.findIndex((e) => e.id === envId);
      if (idx === -1) return state;
      const next = [...state.myEnvironments];
      next[idx] = { ...next[idx], ...patch };
      return { ...state, myEnvironments: next };
    }

    case "removeEnvironment": {
      const byEnv = { ...state.byEnv };
      delete byEnv[action.envId];
      return {
        ...state,
        myEnvironments: state.myEnvironments.filter((e) => e.id !== action.envId),
        byEnv,
      };
    }

    case "patchEnvState": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      return {
        ...state,
        byEnv: { ...state.byEnv, [action.envId]: { ...prev, ...action.patch } },
      };
    }

    case "recordRun": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      return {
        ...state,
        byEnv: {
          ...state.byEnv,
          [action.envId]: { ...prev, runs: [action.run, ...prev.runs] },
        },
      };
    }

    /* A new agent version. Runs pin whichever one is current when they start,
       so minting is the only thing that moves the pairing forward. */
    case "addAgentVersion": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      const list = prev.agentVersions?.length ? prev.agentVersions : [];
      return {
        ...state,
        byEnv: {
          ...state.byEnv,
          [action.envId]: { ...prev, agentVersions: [...list, action.version] },
        },
      };
    }

    case "release": {
      const prev = state.byEnv[action.envId] || emptyEnvState();
      return {
        ...state,
        byEnv: {
          ...state.byEnv,
          [action.envId]: { ...prev, releases: [action.release, ...(prev.releases || [])] },
        },
      };
    }

    case "reset":
      return initialState;

    default:
      return state;
  }
}

const SimStoreContext = createContext(null);

export function SimStoreProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const hydrated = useRef(false);

  // Hydrate once on mount.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      dispatch({
        type: "hydrate",
        payload: raw ? topUpSeed(JSON.parse(raw)) : firstRunState(),
      });
    } catch {
      /* a corrupt prototype cache should never block the screen */
    } finally {
      hydrated.current = true;
    }
  }, []);

  /*
    Persist on every change — but never before hydration has run. The effects
    both fire on mount, and this one sees the pre-hydration `initialState`, so
    without the guard a first visit writes an EMPTY state. If the page is then
    torn down before the hydrated write lands, that empty state is what is
    stored — and because it is no longer null, the seed never runs again and
    the app is permanently blank.
  */
  useEffect(() => {
    if (!hydrated.current) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* quota / private mode — the prototype still works in-memory */
    }
  }, [state]);

  const value = useMemo(() => ({ state, dispatch }), [state]);
  return (
    <SimStoreContext.Provider value={value}>{children}</SimStoreContext.Provider>
  );
}

SimStoreProvider.propTypes = { children: PropTypes.node };

export function useSimStore() {
  const ctx = useContext(SimStoreContext);
  if (!ctx) throw new Error("useSimStore must be used inside <SimStoreProvider>");
  return ctx;
}

/** Convenience hook for one environment's slice of flow state. */
export function useEnvState(envId) {
  const { state, dispatch } = useSimStore();
  const envState = state.byEnv[envId] || emptyEnvState();

  const patch = useCallback(
    (p) => dispatch({ type: "patchEnvState", envId, patch: p }),
    [dispatch, envId],
  );

  const recordRun = useCallback(
    (run) => dispatch({ type: "recordRun", envId, run }),
    [dispatch, envId],
  );

  const release = useCallback(
    (entry) => dispatch({ type: "release", envId, release: entry }),
    [dispatch, envId],
  );

  const addAgentVersion = useCallback(
    (version) => dispatch({ type: "addAgentVersion", envId, version }),
    [dispatch, envId],
  );

  /**
   * Setup progress drives the rail on the workspace and gates the Run button.
   * Evals are optional — a run with no evals still produces traces, it just
   * cannot tell you whether the agent was right.
   */
  const steps = useMemo(
    () => [
      { id: "agent", label: "Connect agent", done: !!envState.agent },
      { id: "scenarios", label: "Add scenarios", done: envState.scenarios.length > 0 },
      { id: "evals", label: "Select evals", done: envState.evals.length > 0, optional: true },
      { id: "run", label: "Run simulation", done: envState.runs.length > 0 },
    ],
    [envState],
  );

  /*
    Env-level readiness. The build view has its own stricter check
    that also requires at least one evaluation — that's the gate
    users must pass BEFORE the env reaches the workspace. Once an env
    exists here, it has already been configured, so leaving evals
    out of this check keeps the workspace chip green even when
    seeded state carries an empty evals array. Removing all evals
    post-creation still surfaces on the Evaluations tab's own gap
    badge, so the signal isn't lost.
  */
  const canRun = !!envState.agent && envState.scenarios.length > 0;
  const nextStep = steps.find((s) => !s.done && !s.optional) || steps[3];

  return { envState, patch, recordRun, addAgentVersion, release, steps, canRun, nextStep };
}

export { emptyEnvState };
