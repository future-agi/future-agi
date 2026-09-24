// Fake progress for the prototype: mock timers drive the build stages. To go
// live, replace the timers with polling getHarnessJob(envId), mapping
// status.stage → MILESTONE — keep this hook's return shape so nothing downstream
// changes. See the note on the enabled effect below for the cleanup contract.
import { useEffect, useReducer, useRef } from "react";
import { useEnvironmentsStore } from "src/sections/simulate/environments/store/useEnvironmentsStore";
import {
  ASK_FALLBACK,
  ASK_REPLIES,
  NARRATION_STAGES,
  asideChips,
} from "./_fixtures/builderNarration";

export const STEP_MS = 380;
export const STAGE_GAP_MS = 500;

// The stages chain themselves — reading the agent, building the world and
// proving the scenarios are each the input to the next, so nothing here waits
// for a click.
const STAGE_ORDER = ["understand", "build", "scenarios"];

const INITIAL = {
  turns: [],
  done: [],
  running: false,
  chips: [],
  failure: null,
};

function reducer(state, action) {
  switch (action.type) {
    case "APPEND_TURN":
      return { ...state, turns: [...state.turns, action.turn] };
    case "PUSH_STEP":
      return {
        ...state,
        turns: state.turns.map((t) =>
          t.id === action.id ? { ...t, steps: [...t.steps, action.step] } : t,
        ),
      };
    case "SET_RUNNING":
      return { ...state, running: action.running };
    case "MARK_DONE":
      return state.done.includes(action.key)
        ? state
        : { ...state, done: [...state.done, action.key] };
    case "SET_CHIPS":
      return { ...state, chips: action.chips };
    case "RESET":
      return INITIAL;
    default:
      return state;
  }
}

// `envId` is accepted but unused: the getHarnessJob poller in the TODO above
// will need it, and the callers already pass it.
export function useBuildProgress({ agentRef, enabled }) {
  const [state, dispatch] = useReducer(reducer, INITIAL);

  const timers = useRef([]);
  const idCounter = useRef(0);
  const agentRefRef = useRef(agentRef);
  agentRefRef.current = agentRef;

  const push = (t) => timers.current.push(t);
  const nextId = (prefix = "t") => `${prefix}-${(idCounter.current += 1)}`;

  // One turn, its steps arriving one per STEP_MS. On the last step the stage is
  // marked done and running clears; onDone chains the next stage.
  const play = ({ id, title, steps, stageKey, onDone, clearChips = true }) => {
    dispatch({ type: "APPEND_TURN", turn: { id, role: "builder", title, steps: [] } });
    dispatch({ type: "SET_RUNNING", running: true });
    if (clearChips) dispatch({ type: "SET_CHIPS", chips: [] });
    steps.forEach((step, i) => {
      push(
        setTimeout(() => {
          dispatch({ type: "PUSH_STEP", id, step });
          if (i !== steps.length - 1) return;
          dispatch({ type: "SET_RUNNING", running: false });
          if (stageKey) dispatch({ type: "MARK_DONE", key: stageKey });
          onDone?.();
        }, STEP_MS * (i + 1)),
      );
    });
  };

  // Kept in a ref so the enabled effect below can depend on nothing but
  // `enabled` and still declare every dependency it uses: `runStage` is rebuilt
  // every render, and listing it would restart the run on each one.
  const runStageRef = useRef(null);

  const runStage = (idx) => {
    const key = STAGE_ORDER[idx];
    const raw = NARRATION_STAGES[key];
    const stage = typeof raw === "function" ? raw(agentRefRef.current) : raw;
    play({
      id: nextId(),
      title: stage.title,
      steps: stage.steps,
      stageKey: key,
      onDone: () => {
        const next = idx + 1;
        if (next < STAGE_ORDER.length) {
          push(setTimeout(() => runStage(next), STAGE_GAP_MS));
        } else {
          dispatch({ type: "SET_CHIPS", chips: asideChips(stage.chips) });
        }
      },
    });
  };

  runStageRef.current = runStage;

  const send = (text) => {
    dispatch({ type: "APPEND_TURN", turn: { id: nextId("u"), role: "user", text } });
    const hit = ASK_REPLIES.find((a) => a.match.test(text));
    play({
      id: nextId(),
      title: null,
      steps: hit ? hit.steps : [ASK_FALLBACK],
      stageKey: null,
      clearChips: false,
    });
  };

  const onChip = (chip) => send(chip);

  // Kick off the first stage when the building stage turns on, and tear the run
  // down the moment it turns off again — on unmount OR on an `enabled` flip back
  // to false within the same mount. The cleanup living here (not in a separate
  // unmount-only effect) is what makes the flip case safe: a stale "building"
  // remount renders once with enabled=true and schedules the stage timers before
  // startPreflight() resets the stage to preflight; without this cleanup those
  // orphaned timers keep firing and stream build stages into the store mid-audit.
  // Keyed on `enabled` (no ref guard) so a StrictMode remount re-runs cleanly.
  useEffect(() => {
    if (!enabled) return undefined;
    runStageRef.current(0);
    return () => {
      // Clear timers AND reset the reducer — runStage(0) synchronously appended a
      // turn and set running:true, so a bare timer-clear would leak that (and an
      // empty console turn + a "running" pill) into the store on an enabled flip.
      timers.current.forEach(clearTimeout);
      timers.current = [];
      dispatch({ type: "RESET" });
    };
  }, [enabled]);

  // Mirror the milestone state to the store so the header, which sits outside
  // this pane, reads the same source of truth.
  useEffect(() => {
    useEnvironmentsStore.getState().setBuildProgress({
      done: state.done,
      running: state.running,
      failure: state.failure,
    });
  }, [state.done, state.running, state.failure]);

  return {
    done: state.done,
    running: state.running,
    failure: state.failure,
    turns: state.turns,
    chips: state.chips,
    send,
    onChip,
  };
}
