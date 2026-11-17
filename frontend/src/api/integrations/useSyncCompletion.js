import { useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { scopedConfig } from "src/sections/settings/integrations/scope";

const POLL_MS = 2000;
const MAX_WAIT_MS = 5 * 60 * 1000;
export const SYNC_READ_TIMEOUT_MS = 10000;
const TERMINAL_CONNECTION_STATES = ["active", "error", "paused"];
const resultOf = (response) => response?.data?.result || response?.data;
const logsOf = (response) => {
  const result = resultOf(response);
  return (
    result?.sync_logs ||
    result?.results ||
    (Array.isArray(result) ? result : [])
  );
};

function operation(id, previousIds = new Set()) {
  const current = {
    id,
    previousIds,
    controller: new AbortController(),
    timedOut: false,
  };
  current.timer = setTimeout(() => {
    current.timedOut = true;
    current.controller.abort();
  }, MAX_WAIT_MS);
  return current;
}

function stop(current) {
  if (!current) return;
  clearTimeout(current.timer);
  current.controller.abort();
}

// Axios timeouts alone do not cover queued interceptors or a transport that
// never settles. Race every awaited operation against the one lifetime signal.
function within(current, work) {
  const { signal } = current.controller;
  return new Promise((resolve, reject) => {
    const abort = () => {
      const error = new Error(
        current.timedOut
          ? "Sync request timed out. Refresh the status before trying again."
          : "Sync monitoring canceled",
      );
      error.code = current.timedOut ? "ETIMEDOUT" : "ERR_CANCELED";
      reject(error);
    };
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    Promise.resolve()
      .then(() => {
        if (signal.aborted) return undefined;
        return work();
      })
      .then(resolve, reject)
      .finally(() => signal.removeEventListener("abort", abort));
  });
}

function pause(signal) {
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, POLL_MS);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

// The dispatch response has no job ID, and ACTIVE can mean either queued or
// finished. A NEW completed_at row is the witness, including failed exports.
// The same lifetime includes snapshot, dispatch, polling and reconciliation.
export default function useSyncCompletion(
  queryClient,
  workspaceId,
  keys,
  connectionId,
) {
  const active = useRef(null);
  const inFlight = useRef(new Map());
  const [status, setStatus] = useState("idle");
  useEffect(() => {
    const pending = inFlight.current;
    setStatus("idle");
    return () => {
      stop(active.current);
      pending.forEach(({ current }) => stop(current));
    };
  }, [workspaceId, connectionId]);

  const readLogs = (id, signal) =>
    axios.get(
      endpoints.integrations.syncLogs,
      scopedConfig(workspaceId, {
        params: { connection_id: id },
        signal,
        timeout: SYNC_READ_TIMEOUT_MS,
      }),
    );

  // Reserve synchronously, before React Query's asynchronous onMutate or its
  // pending notification. Re-entry shares the entire operation, not just POST:
  // aborting a request cannot undo an export the server has already queued.
  const run = (id, work, previousIds) => {
    const scope = JSON.stringify([workspaceId, id]);
    if (inFlight.current.has(scope)) return inFlight.current.get(scope).promise;
    stop(active.current);
    // Register the lifetime before queueing work, so same-turn cleanup can
    // abort it even before React Query starts onMutate. Carry this exact
    // operation through the mutation; never borrow a later active operation.
    const current = operation(id, previousIds);
    active.current = current;
    const promise = Promise.resolve()
      .then(() => work(current))
      .finally(() => {
        stop(current);
        if (inFlight.current.get(scope)?.promise === promise) {
          inFlight.current.delete(scope);
        }
      });
    inFlight.current.set(scope, { current, promise });
    return promise;
  };

  const prepare = async (current) => {
    const { signal } = current.controller;
    if (!signal.aborted) setStatus("idle");
    try {
      const previous = await within(current, () =>
        readLogs(current.id, signal),
      );
      if (!signal.aborted) {
        current.previousIds = new Set(logsOf(previous).map((log) => log.id));
      }
      return current;
    } catch (error) {
      stop(current);
      throw error;
    }
  };

  const dispatch = (current) =>
    within(current, () =>
      axios.post(
        endpoints.integrations.connections.syncNow(current.id),
        {},
        scopedConfig(workspaceId, {
          signal: current.controller.signal,
          timeout: MAX_WAIT_MS,
        }),
      ),
    );

  const complete = async (id, current) => {
    const { signal } = current.controller;
    if (signal.aborted) return;
    setStatus("checking");
    let confirmed = false;
    let reconciling = false;
    const queryKeys = [keys.connection(id), keys.syncLogs(id), keys.lists()];
    const cancelRefetches = () => {
      if (reconciling) {
        queryKeys.forEach((queryKey) => {
          queryClient.cancelQueries({ queryKey });
        });
      }
    };
    signal.addEventListener("abort", cancelRefetches, { once: true });
    try {
      while (!signal.aborted) {
        const [connection, history] = await within(current, () =>
          Promise.all([
            axios.get(
              endpoints.integrations.connections.detail(id),
              scopedConfig(workspaceId, {
                signal,
                timeout: SYNC_READ_TIMEOUT_MS,
              }),
            ),
            readLogs(id, signal),
          ]),
        );
        if (signal.aborted) return;
        queryClient.setQueryData(keys.connection(id), connection);
        queryClient.setQueryData(keys.syncLogs(id), history);
        const completed =
          TERMINAL_CONNECTION_STATES.includes(resultOf(connection)?.status) &&
          logsOf(history).some(
            (log) => log.completed_at && !current.previousIds.has(log.id),
          );
        if (completed) {
          // Query functions consume their QueryClient signal. Cancel those
          // refetches when this operation ends; the race also covers adapters
          // that ignore cancellation. Never restart the deadline here.
          reconciling = true;
          await within(current, () =>
            Promise.all(
              queryKeys.map((queryKey) =>
                queryClient.invalidateQueries(
                  { queryKey },
                  { throwOnError: true },
                ),
              ),
            ),
          );
          if (signal.aborted) return;
          confirmed = true;
          break;
        }
        await within(current, () => pause(signal));
      }
    } catch {
      // A failed refresh does not prove a failed export. Do not dispatch again.
    } finally {
      signal.removeEventListener("abort", cancelRefetches);
      cancelRefetches();
      if (!signal.aborted || current.timedOut) {
        setStatus(confirmed ? "completed" : "unconfirmed");
        if (!confirmed) {
          enqueueSnackbar(
            "Sync started, but completion could not be confirmed. Refresh the status to check its history.",
            { variant: "warning" },
          );
        }
      }
      stop(current);
    }
  };

  const checkStatus = (id) => {
    const pending = inFlight.current.get(JSON.stringify([workspaceId, id]));
    if (pending) return pending.promise;
    const previous = active.current;
    if (!previous || previous.id !== id || !previous.controller.signal.aborted)
      return Promise.resolve();
    // Recovery owns a synchronous lifetime too, while retaining the snapshot
    // from the existing export. It must never POST another sync.
    return run(id, (current) => complete(id, current), previous.previousIds);
  };

  const fail = (current) => {
    if (current?.timedOut && active.current === current)
      setStatus("unconfirmed");
    stop(current);
  };
  return { run, prepare, dispatch, complete, checkStatus, fail, status };
}
