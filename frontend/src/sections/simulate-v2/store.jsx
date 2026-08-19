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
} from "react";

const STORAGE_KEY = "fagi.simulate.v2.prototype";

const emptyEnvState = () => ({
  agent: null, // { typeId, values, connectedAt }
  scenarios: [], // scenario rows chosen or generated
  scenarioSource: null, // 'templates' | 'chat' | 'agent'
  evals: [], // eval ids
  runs: [], // completed run summaries
});

const initialState = {
  myEnvironments: [], // environments the user created / adopted
  byEnv: {}, // envId -> emptyEnvState()
  activeRun: null,
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

    case "reset":
      return initialState;

    default:
      return state;
  }
}

const SimStoreContext = createContext(null);

export function SimStoreProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  // Hydrate once on mount.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) dispatch({ type: "hydrate", payload: JSON.parse(raw) });
    } catch {
      /* a corrupt prototype cache should never block the screen */
    }
  }, []);

  // Persist on every change.
  useEffect(() => {
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

  const canRun = !!envState.agent && envState.scenarios.length > 0;
  const nextStep = steps.find((s) => !s.done && !s.optional) || steps[3];

  return { envState, patch, recordRun, steps, canRun, nextStep };
}

export { emptyEnvState };
