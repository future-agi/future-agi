// Build progress for the /build page. The real path polls getHarnessJob and
// derives the { done, running, failure } milestone slice from the live job; the
// mock path (a draft that could not be built for real — `mockMode`) keeps the
// prototype's fixture timer-walk so those sources do not regress. Both paths keep
// this hook's return shape so nothing downstream changes.
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { harnessJobQuery } from "src/api/simulate-environments/environment";
import { jobToBuildProgress } from "src/sections/simulate/environments/buildEnvironment/buildPipeline.constants";
import {
  ASK_FALLBACK,
  ASK_REPLIES,
  NARRATION_STAGES,
  asideChips,
} from "./_fixtures/builderNarration";

export const STEP_MS = 380;
export const STAGE_GAP_MS = 500;

// The milestone order narration + chips key off, in build order.
const STAGE_ORDER = ["understand", "build", "scenarios"];

const narrationFor = (key, agentRef) => {
  const raw = NARRATION_STAGES[key];
  if (!raw) return null;
  return typeof raw === "function" ? raw(agentRef) : raw;
};

// A tiny fixture-reply engine for the console's "Ask, correct or steer" box —
// shared by both paths (there is no build-time chat API).
function useConsoleReplies() {
  const idc = useRef(0);
  const [replies, setReplies] = useState([]);
  const send = (text) => {
    const uid = (idc.current += 1);
    const hit = ASK_REPLIES.find((a) => a.match.test(text));
    setReplies((prev) => [
      ...prev,
      { id: `u-${uid}`, role: "user", text },
      { id: `t-ask-${uid}`, role: "builder", title: null, steps: hit ? hit.steps : [ASK_FALLBACK] },
    ]);
  };
  return { replies, send, onChip: (chip) => send(chip) };
}

// ---- Real path: poll the live job -----------------------------------------

function useRealBuildProgress({ envId, agentRef, enabled }) {
  const jobQuery = useQuery(harnessJobQuery(envId, { enabled }));
  const job = jobQuery.data;
  const { done, running, failure } = useMemo(
    () => jobToBuildProgress(job),
    [job],
  );

  const agentRefRef = useRef(agentRef);
  agentRefRef.current = agentRef;

  // Append a stage's narration turn the first time its milestone completes.
  const [turns, setTurns] = useState([]);
  const seen = useRef(new Set());
  useEffect(() => {
    if (!enabled) return;
    done.forEach((key) => {
      if (seen.current.has(key)) return;
      seen.current.add(key);
      const stage = narrationFor(key, agentRefRef.current);
      if (stage) {
        setTurns((prev) => [
          ...prev,
          { id: `t-${key}`, role: "builder", title: stage.title, steps: stage.steps },
        ]);
      }
    });
  }, [done, enabled]);

  const chips = useMemo(() => {
    if (!done.includes("scenarios")) return [];
    const stage = narrationFor("scenarios", agentRefRef.current);
    return stage ? asideChips(stage.chips) : [];
  }, [done]);

  const { replies, send, onChip } = useConsoleReplies();

  return { done, running, failure, turns: [...turns, ...replies], chips, send, onChip, job };
}

// ---- Mock path: the prototype fixture timer-walk (mockMode only) -----------

const MOCK_INITIAL = { turns: [], done: [], running: false, chips: [], failure: null };

function mockReducer(state, action) {
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
      return MOCK_INITIAL;
    default:
      return state;
  }
}

function useMockBuildProgress({ agentRef, enabled }) {
  const [state, dispatch] = useReducer(mockReducer, MOCK_INITIAL);
  const timers = useRef([]);
  const idCounter = useRef(0);
  const agentRefRef = useRef(agentRef);
  agentRefRef.current = agentRef;

  const push = (t) => timers.current.push(t);
  const nextId = (prefix = "t") => `${prefix}-${(idCounter.current += 1)}`;

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

  const runStage = (idx) => {
    const key = STAGE_ORDER[idx];
    const stage = narrationFor(key, agentRefRef.current);
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

  const send = (text) => {
    dispatch({ type: "APPEND_TURN", turn: { id: nextId("u"), role: "user", text } });
    const hit = ASK_REPLIES.find((a) => a.match.test(text));
    play({ id: nextId(), title: null, steps: hit ? hit.steps : [ASK_FALLBACK], stageKey: null, clearChips: false });
  };

  // Kick off the first stage when building turns on, and tear down on unmount OR
  // an `enabled` flip back to false in the same mount — the cleanup living here
  // (not a separate unmount-only effect) is what makes the flip case safe: a
  // stale "building" remount would otherwise schedule timers that keep streaming
  // stages, and this clears them.
  useEffect(() => {
    if (!enabled) return undefined;
    runStage(0);
    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
      dispatch({ type: "RESET" });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return {
    done: state.done,
    running: state.running,
    failure: state.failure,
    turns: state.turns,
    chips: state.chips,
    send,
    onChip: (chip) => send(chip),
    job: undefined,
  };
}

// ---- Public hook ----------------------------------------------------------

export function useBuildProgress({ envId, agentRef, enabled, mockMode = false }) {
  const real = useRealBuildProgress({ envId, agentRef, enabled: enabled && !mockMode });
  const mock = useMockBuildProgress({ agentRef, enabled: enabled && mockMode });
  return mockMode ? mock : real;
}
