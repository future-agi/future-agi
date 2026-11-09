import { useCallback, useEffect, useRef, useState } from "react";

import { useDeploymentsApi } from "../api/DeploymentsApiContext";
import { isTransient } from "../api/queries";

/**
 * Follow one deployment log (SSE through the gateway).
 *
 * EE ends every host-delegated response when its customer assertion expires
 * (about 45 s), and the transport then closes without the stream's own
 * `event: end`. That is not the end of the log: a build log reopens after the
 * last line it saw (Last-Event-ID = seq), a runtime log reopens with
 * `tail: 0` so no line repeats. Only a server `end` frame, a failure, or the
 * caller stops the stream.
 *
 * A runtime log follows one container. When that container stops (a new
 * revision or a rollback took over), the stream follows the one serving next
 * from its first line; it ends only when nothing is running. EE stops the old
 * container before the new one takes the port, so a reopen can catch the old
 * one still draining: lines are deduplicated, and the switch is noted where
 * the lines' revision changes, not where a stream happened to end.
 *
 * An open that fails transiently (the assertion refused with 401, or the
 * gateway unavailable) is tried again a few times with a growing pause before
 * the failure is shown; any other refusal is shown at once.
 */
const MAX_LINES = 5000;
const RUNTIME_TAIL = 200;
const REOPEN_DELAY_MS = 300;
// A cut that comes this soon after opening, with no line in between, counts
// toward giving up; five in a row means something is wrong, not a timeout.
const QUICK_CUT_MS = 2000;
const MAX_QUICK_CUTS = 5;
const CONTAINER_STOPPED = "container stopped";
const NEXT_CONTAINER_DELAY_MS = 1500;
const MAX_CONTAINER_HOPS = 20;
const MAX_TRANSIENT_FAILURES = 3;
const TRANSIENT_DELAY_MS = 400;

export const LOG_STATE = Object.freeze({
  IDLE: "idle",
  CONNECTING: "connecting",
  STREAMING: "streaming",
  ENDED: "ended",
  ERROR: "error",
});

export function useLogStream(
  id,
  { source = "build", revision, enabled = true } = {},
) {
  const api = useDeploymentsApi();
  const [lines, setLines] = useState([]);
  const [state, setState] = useState(LOG_STATE.IDLE);
  const [endReason, setEndReason] = useState(null);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const reconnects = useRef(0);

  useEffect(() => {
    if (!enabled || !id) return undefined;
    let stopped = false;
    let current = null;
    let lastSeq = null;
    let first = true;
    let quickCuts = 0;
    let hops = 0;
    let transientFailures = 0;
    let lastRevision = null;
    const seenRuntime = new Set();
    setLines([]);
    setEndReason(null);
    setError(null);
    setState(LOG_STATE.CONNECTING);
    reconnects.current = 0;

    const append = (entry) =>
      setLines((previous) => {
        const next =
          previous.length >= MAX_LINES ? previous.slice(1) : previous.slice();
        next.push(entry);
        return next;
      });

    const open = async () => {
      while (!stopped) {
        let sawEnd = null;
        let sawLine = false;
        const openedAt = Date.now();
        const wasFirst = first;
        current = api.logs(id, {
          source,
          revision,
          tail: source === "runtime" ? (first ? RUNTIME_TAIL : 0) : undefined,
          lastEventId:
            source === "build" && lastSeq !== null ? String(lastSeq) : null,
          onEvent: (event) => {
            if (event.event === "end") {
              sawEnd = event.json?.reason || "ended";
              return;
            }
            if (event.event !== "log" || !event.json) return;
            const entry = event.json;
            if (source === "build" && Number.isSafeInteger(entry.seq)) {
              if (lastSeq !== null && entry.seq <= lastSeq) return;
              lastSeq = entry.seq;
            }
            if (source === "runtime") {
              // Docker stamps each line to the nanosecond: a re-read of the
              // same container (it may still be stopping) repeats nothing.
              const seenKey = `${entry.revision}|${entry.at}|${entry.line}`;
              if (seenRuntime.has(seenKey)) return;
              seenRuntime.add(seenKey);
            }
            sawLine = true;
            transientFailures = 0;
            setState(LOG_STATE.STREAMING);
            const lineRevision = entry.revision ?? revision ?? null;
            if (
              source === "runtime" &&
              lastRevision !== null &&
              lineRevision !== null &&
              lineRevision !== lastRevision
            ) {
              append({
                key: `runtime-switch-${lastRevision}-${lineRevision}-${entry.at}`,
                at: entry.at,
                line: `Revision ${lastRevision} stopped. Following revision ${lineRevision}.`,
                note: true,
              });
            }
            if (lineRevision !== null) lastRevision = lineRevision;
            append({
              key: `${source}-${entry.seq ?? `${Date.now()}-${Math.random()}`}`,
              at: entry.at,
              line: entry.line,
              revision: entry.revision ?? revision ?? null,
            });
          },
        });
        const result = await current.done;
        first = false;
        if (stopped || result?.closed) return;
        if (result && result.ok === false) {
          if (
            isTransient(result) &&
            transientFailures < MAX_TRANSIENT_FAILURES
          ) {
            transientFailures += 1;
            // Nothing was read, so a runtime log still wants its tail.
            if (!sawLine) first = wasFirst;
            reconnects.current += 1;
            await new Promise((resolve) =>
              setTimeout(
                resolve,
                TRANSIENT_DELAY_MS * 2 ** (transientFailures - 1),
              ),
            );
            continue;
          }
          setError(result);
          setState(LOG_STATE.ERROR);
          return;
        }
        if (
          sawEnd === CONTAINER_STOPPED &&
          source === "runtime" &&
          hops < MAX_CONTAINER_HOPS
        ) {
          // Another revision may be serving now: follow it from its start.
          hops += 1;
          first = true;
          await new Promise((resolve) =>
            setTimeout(resolve, NEXT_CONTAINER_DELAY_MS),
          );
          continue;
        }
        if (sawEnd) {
          setEndReason(sawEnd);
          setState(LOG_STATE.ENDED);
          return;
        }
        // The transport ended without the log's own end: reopen.
        quickCuts =
          !sawLine && Date.now() - openedAt < QUICK_CUT_MS ? quickCuts + 1 : 0;
        if (quickCuts >= MAX_QUICK_CUTS) {
          setError({
            code: "stream_interrupted",
            message: "The log stream keeps closing.",
          });
          setState(LOG_STATE.ERROR);
          return;
        }
        reconnects.current += 1;
        await new Promise((resolve) => setTimeout(resolve, REOPEN_DELAY_MS));
      }
    };
    open();
    return () => {
      stopped = true;
      current?.close();
    };
  }, [api, id, source, revision, enabled, attempt]);

  const restart = useCallback(() => setAttempt((n) => n + 1), []);

  return {
    lines,
    state,
    endReason,
    error,
    restart,
    reconnects: reconnects.current,
  };
}
