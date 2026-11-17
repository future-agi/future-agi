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

/**
 * A command on its way to the sandbox. `waking`: the sandbox is hibernated, so
 * EE resumes it before the command starts; `runningLimit` is the workspace's
 * cap on running sandboxes, for the words when that resume is refused.
 */
export function startExec(
  command,
  { waking = false, runningLimit = null } = {},
) {
  return { ...EXEC_IDLE, status: "starting", command, waking, runningLimit };
}

/**
 * The words for a resume EE refused at the workspace's running cap (409 with
 * reason quota_exceeded), or null for any other refusal. EE's own words speak
 * of "this call" and say "destroy" where the page says "delete".
 */
function wakeRefusal(state, result) {
  if (!state.waking) return null;
  if (result.reason !== "quota_exceeded" && result.code !== "quota_exceeded")
    return null;
  const max = state.runningLimit;
  const limit = Number.isSafeInteger(max)
    ? `its limit of ${max} running sandbox${max === 1 ? "" : "es"}`
    : "its limit of running sandboxes";
  return `The sandbox couldn't resume, so the command didn't run. This workspace is at ${limit}. Hibernate or delete one, then run the command again.`;
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
      error:
        wakeRefusal(state, result) ||
        result.message ||
        "The command could not be started.",
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
  if (state.status === "starting")
    return state.waking ? "Resuming the sandbox…" : "Starting…";
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
