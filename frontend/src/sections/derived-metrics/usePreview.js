import { useCallback, useEffect, useState } from "react";
import { metricErrorState, metricsApi } from "./api";
import { useMetricsEnabled } from "./useMetricsEnabled";

// Serialize even across builder unmount/remount. Aborting a transport does not
// prove its promise settled; never overlap it with the replacement request.
let previewTail = Promise.resolve();
const wait = (ms, signal) =>
  new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });

export default function usePreview({
  scope,
  definition,
  window,
  enabled = true,
}) {
  const { enabled: rolloutEnabled } = useMetricsEnabled();
  const ready = Boolean(
    enabled &&
      rolloutEnabled &&
      scope.organizationId &&
      scope.workspaceId &&
      scope.projectId &&
      definition,
  );
  const identity = JSON.stringify({ scope, definition, window, ready });
  const [retryVersion, setRetryVersion] = useState(0);
  const [result, setResult] = useState({});
  const retry = useCallback(
    () => setRetryVersion((version) => version + 1),
    [],
  );
  useEffect(() => {
    const {
      scope: capturedScope,
      definition: recipe,
      window: range,
      ready: canRun,
    } = JSON.parse(identity);
    if (!canRun) return undefined;
    const controller = new AbortController();
    const { signal } = controller;
    const publish = (state, data, error) => {
      if (!signal.aborted)
        setResult({ identity, retryVersion, state, data, error });
    };
    publish("loading");
    const timer = setTimeout(() => {
      const execute = async () => {
        for (let attempt = 0; attempt < 2; attempt += 1) {
          if (signal.aborted) return;
          try {
            const data = await metricsApi.preview({
              scope: capturedScope,
              signal,
              data: {
                definition: recipe,
                ...range,
                include_samples: true,
                request_id: crypto.randomUUID(),
              },
            });
            publish("ready", data);
            return;
          } catch (error) {
            if (signal.aborted) return;
            const state = metricErrorState(error);
            if (state === "busy" && attempt === 0) {
              publish("busy_retrying");
              try {
                await wait(1000, signal);
              } catch {
                return;
              }
            } else {
              publish(state, undefined, error);
              return;
            }
          }
        }
      };
      previewTail = previewTail.then(execute, execute);
    }, 600);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [identity, retryVersion]);
  if (!ready) return { state: "idle", retry };
  // A previous recipe is never displayed during the debounce or after a scope
  // change, including the render before effect cleanup runs.
  if (result.identity !== identity || result.retryVersion !== retryVersion)
    return { state: "loading", retry };
  return { ...result, retry };
}
