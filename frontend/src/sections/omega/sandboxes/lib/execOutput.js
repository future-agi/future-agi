/**
 * State of one streamed command in the Run tab, folded from the exec SSE
 * events (`started` {execId}, `stdout`/`stderr` {data}, `exit` {...},
 * `error` {message}) and stream failures. Pure, so it is unit-tested.
 */
export const EXEC_IDLE = Object.freeze({
  status: "idle",
  execId: null,
  chunks: [],
  outcome: null,
  error: null,
});

const MAX_CHUNKS = 5000;

export function startExec(command) {
  return { ...EXEC_IDLE, status: "starting", command };
}

export function applyExecEvent(state, event) {
  const data = event?.json;
  switch (event?.event) {
    case "started":
      return { ...state, status: "running", execId: data?.execId ?? null };
    case "stdout":
    case "stderr": {
      if (typeof data?.data !== "string" || data.data.length === 0)
        return state;
      const chunks = [
        ...state.chunks,
        { stream: event.event, text: data.data },
      ];
      return {
        ...state,
        chunks: chunks.length > MAX_CHUNKS ? chunks.slice(-MAX_CHUNKS) : chunks,
      };
    }
    case "exit":
      return { ...state, status: "done", outcome: data ?? {} };
    case "error":
      return {
        ...state,
        status: "failed",
        error: data?.message || "The command stream reported an error.",
      };
    default:
      return state;
  }
}

/** A stream that stopped without an `exit` event. */
export function applyExecEnd(state, result) {
  if (state.status === "done" || state.status === "failed") return state;
  if (result && result.ok === false) {
    return {
      ...state,
      status: "failed",
      error: result.message || "The command could not be started.",
    };
  }
  if (result?.closed) return { ...state, status: "closed" };
  return {
    ...state,
    status: "failed",
    error: "The command stream ended before the command finished.",
  };
}

/**
 * One-line summary for the output header. A failure's reason can be a long
 * sentence, so the header says only "Failed" and the reason is shown in full
 * under it (RunOutput).
 */
export function describeOutcome(state) {
  if (state.status === "starting") return "Starting…";
  if (state.status === "running") return "Running…";
  if (state.status === "closed") return "Stopped watching";
  if (state.status === "failed") return "Failed";
  const outcome = state.outcome;
  if (!outcome) return "";
  if (outcome.cancelled) return "Cancelled";
  if (outcome.timedOut) return "Timed out";
  if (outcome.error) return outcome.error;
  const code =
    outcome.exitCode === null ? "no exit code" : `exit ${outcome.exitCode}`;
  return outcome.signal ? `${code} (${outcome.signal})` : code;
}

export function isExecLive(state) {
  return state.status === "starting" || state.status === "running";
}
