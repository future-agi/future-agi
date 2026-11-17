import { failure, omegaFetch as defaultOmegaFetch } from "./omegaClient";
import { OMEGA_STATE } from "./omegaStates";
import { createSseParser } from "./sseParser";
import {
  BUFFERING_TIMEOUT_MS,
  POLL_GRACE_MS,
  POLL_MAX_WAIT_MS,
  POLL_MIN_WAIT_MS,
  STREAM_MODE,
  currentStreamMode,
  settleOnPolling,
  transportQuery,
} from "./streamMode";

export { createSseParser } from "./sseParser";

/**
 * Server-sent events through the Omega gateway. EventSource cannot send an
 * Authorization header, so streams are read with fetch: every (re)connect
 * mints a fresh customer assertion and resumes with Last-Event-ID.
 */

// A cut this soon after opening, with no event in between, is suspicious;
// `maxEmptyCuts` of them in a row is a broken route, not a key's expiry.
const QUICK_CUT_MS = 2000;

const sleep = (ms, signal) =>
  new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });

// An empty window shorter than this is a close, not a long-poll deadline.
const pollFloorMs = (mode) =>
  mode === STREAM_MODE.POLL ? POLL_MIN_WAIT_MS / 2 : Infinity;

const streamFailure = (code) =>
  failure({ code, fallbackState: OMEGA_STATE.UNAVAILABLE });

/**
 * openOmegaStream(path, options) -> { close(), done }
 *
 * options:
 * - onEvent({id, event, data, json}); onState(failure) for each failed or
 *   lost connection; onOpen({ attempt, mode, path }) for each connection that
 *   opened, so a consumer can reset what it shows live.
 * - method, json, query, lastEventId, signal.
 * - resumable (default: GET only, because re-sending a POST such as sandbox
 *   exec would run it again), maxReconnects, retryDelayMs.
 * - terminalEvents: the event names that end this stream (exec: ["exit",
 *   "error"]; deployment logs: ["end"]). With them, a clean end that carried
 *   none is a cut, not the end (READINESS L06: EE ends a response when its key
 *   expires, and the gateway or a server roll can do the same): a resumable
 *   stream comes straight back with Last-Event-ID. Without them (the default),
 *   any clean end is the end, as before.
 * - reattach({ firstEvent, lastEvent, lastEventId }) -> path | null: where a
 *   non-resumable stream continues after a cut (a GET that replays after
 *   Last-Event-ID, such as a sandbox exec's events route). The stream then
 *   reads that route, resumably, until a terminal event.
 * - transport: "stream" (default, plain SSE) or "auto" (CH-12): ask for an
 *   immediate first byte; with none within bufferingTimeoutMs, switch to
 *   long-poll windows of pollWaitMs for this stream and every later one in
 *   this document (streamMode.js). Only for a resumable stream with
 *   terminalEvents on a route that implements the transport contract.
 *
 * `done` resolves with { ended: true } when the stream finished, { closed:
 * true } after close()/abort, or the `{ ok: false, state, ... }` failure that
 * stopped it. Every resolved value also carries `mode`.
 */
export function openOmegaStream(path, options = {}) {
  const {
    onEvent = () => {},
    onState = () => {},
    onOpen = () => {},
    method = "GET",
    json,
    query,
    signal,
    resumable = String(method).toUpperCase() === "GET",
    terminalEvents = null,
    reattach = null,
    transport = STREAM_MODE.STREAM,
    bufferingTimeoutMs = BUFFERING_TIMEOUT_MS,
    pollWaitMs = POLL_MAX_WAIT_MS,
    maxReconnects = 5,
    maxEmptyCuts = 5,
    retryDelayMs = 1000,
    omegaFetch = defaultOmegaFetch,
  } = options;
  const terminal = Array.isArray(terminalEvents)
    ? new Set(terminalEvents)
    : null;
  const auto = transport === "auto";
  if (auto && (!resumable || !terminal)) {
    throw new TypeError(
      'transport "auto" needs a resumable stream with terminalEvents',
    );
  }
  const controller = new AbortController();
  if (signal) {
    if (signal.aborted) controller.abort();
    else
      signal.addEventListener("abort", () => controller.abort(), {
        once: true,
      });
  }
  let lastEventId = options.lastEventId ?? null;
  let target = {
    path,
    method,
    json,
    resumable,
    query,
  };
  let mode = auto ? currentStreamMode() : null;
  let firstEvent = null;
  let lastEvent = null;

  const finish = (value) => (auto ? { ...value, mode } : value);

  /** Switch a cut, non-resumable stream to its reattach route, if it has one. */
  const switchToReattach = () => {
    const next = reattach?.({ firstEvent, lastEvent, lastEventId }) ?? null;
    if (!next) return false;
    target = { path: next, method: "GET", json: undefined, resumable: true };
    return true;
  };

  /** One connection: resolves `{ outcome, sawEvent, sawTerminal }`. */
  const connect = async (attempt) => {
    const connection = new AbortController();
    const stop = () => connection.abort();
    controller.signal.addEventListener("abort", stop, { once: true });
    let firstByte = false;
    let buffered = false;
    let overdue = false;
    let watchdog = null;
    let armed = false;
    // The timers start when the request goes out (omegaFetch's onSend, after
    // the key is minted, again on its one stale-key retry), so a slow session
    // exchange is never read as a buffering proxy. An omegaFetch that does not
    // call onSend gets them once it answers.
    const arm = () => {
      if (!auto) return;
      armed = true;
      clearTimeout(watchdog);
      watchdog =
        mode === STREAM_MODE.STREAM
          ? setTimeout(() => {
              if (firstByte) return;
              buffered = true;
              connection.abort();
            }, bufferingTimeoutMs)
          : setTimeout(() => {
              overdue = true;
              connection.abort();
            }, pollWaitMs + POLL_GRACE_MS);
    };
    // A refusal is a whole answer, not a held stream: stop timing it, so a
    // pause before omegaFetch's own retry (a 503 that asked to wait, a stale
    // key's re-mint) is never read as buffering. The retry's onSend re-arms.
    // An OK answer keeps the timer: a proxy can send headers and hold the body.
    const answered = (response) => {
      if (!response?.ok) clearTimeout(watchdog);
    };
    const state = { sawEvent: false, sawTerminal: false };
    const cleanup = () => {
      clearTimeout(watchdog);
      controller.signal.removeEventListener("abort", stop);
    };
    const lost = () => {
      if (controller.signal.aborted) return { outcome: "closed" };
      if (buffered) return { outcome: "buffered" };
      // A poll window overdue past its grace is treated like a dropped socket.
      return { outcome: "interrupted", ...state };
    };
    let result;
    try {
      result = await omegaFetch(target.path, {
        method: target.method,
        json: target.json,
        query: auto
          ? { ...(target.query || {}), ...transportQuery(mode, pollWaitMs) }
          : target.query,
        raw: true,
        signal: connection.signal,
        headers: lastEventId ? { "Last-Event-ID": lastEventId } : {},
        onSend: arm,
        onAnswer: answered,
      });
    } catch (error) {
      cleanup();
      if (controller.signal.aborted || buffered || overdue) return lost();
      throw error;
    }
    if (!result.ok) {
      cleanup();
      return { outcome: "refused", result };
    }
    if (!armed) arm();
    onOpen({ attempt, mode: mode ?? STREAM_MODE.STREAM, path: target.path });
    const parser = createSseParser((event) => {
      state.sawEvent = true;
      if (!firstEvent) firstEvent = event;
      lastEvent = event;
      if (terminal?.has(event.event)) state.sawTerminal = true;
      onEvent(event);
    });
    const reader = result.response.body.getReader();
    const decoder = new TextDecoder();
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        firstByte = true;
        parser.push(decoder.decode(value, { stream: true }));
        lastEventId = parser.lastEventId ?? lastEventId;
      }
      parser.push(decoder.decode());
      lastEventId = parser.lastEventId ?? lastEventId;
      return { outcome: "ended", ...state };
    } catch {
      lastEventId = parser.lastEventId ?? lastEventId;
      return lost();
    } finally {
      cleanup();
    }
  };

  const run = async () => {
    let failures = 0;
    let emptyCuts = 0;
    let attempt = 0;
    while (!controller.signal.aborted) {
      attempt += 1;
      const openedAt = Date.now();
      const step = await connect(attempt);
      if (step.outcome === "closed") return finish({ closed: true });
      if (step.sawEvent) {
        failures = 0;
        emptyCuts = 0;
      }
      if (step.outcome === "buffered") {
        // CH-12: the proxy is holding the stream. Poll from here on.
        mode = STREAM_MODE.POLL;
        settleOnPolling();
        continue;
      }
      if (step.outcome === "refused") {
        onState(step.result);
        if (
          step.result.state === OMEGA_STATE.UNAVAILABLE &&
          target.resumable &&
          failures < maxReconnects
        ) {
          failures += 1;
          await sleep(retryDelayMs * failures, controller.signal);
          continue;
        }
        return finish(step.result);
      }
      if (step.outcome === "ended") {
        if (!terminal || step.sawTerminal) return finish({ ended: true });
        if (!target.resumable && !switchToReattach()) {
          const cut = streamFailure("stream_cut");
          onState(cut);
          return finish(cut);
        }
        // A long-poll window, a key's expiry or a server roll: come straight
        // back, unless the route keeps closing at once with nothing in it.
        // A poll window that ran to its deadline empty is not such a close,
        // however short the caller made it.
        const quick =
          Date.now() - openedAt < Math.min(QUICK_CUT_MS, pollFloorMs(mode));
        if (!step.sawEvent && quick) {
          emptyCuts += 1;
          if (emptyCuts >= maxEmptyCuts) {
            const cut = streamFailure("stream_cut");
            onState(cut);
            return finish(cut);
          }
          await sleep(retryDelayMs * emptyCuts, controller.signal);
        }
        continue;
      }
      // Interrupted: the socket dropped (EE destroys a guarded response it
      // ends, a proxy resets it, the network blinks).
      if (step.sawTerminal) return finish({ ended: true });
      const resumes = target.resumable || switchToReattach();
      if (!resumes || failures >= maxReconnects) {
        const lostStream = streamFailure("stream_interrupted");
        onState(lostStream);
        return finish(lostStream);
      }
      failures += 1;
      await sleep(retryDelayMs * failures, controller.signal);
    }
    return finish({ closed: true });
  };
  const done = run();
  return { close: () => controller.abort(), done };
}
