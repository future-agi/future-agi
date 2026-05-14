import { useCallback, useEffect, useRef } from "react";
import axios, { endpoints } from "src/utils/axios";
import useImagineStore from "./useImagineStore";

/**
 * Background analysis for dynamic widgets via REST API + DB polling.
 *
 * Flow:
 * 1. Detect widgets with `dynamicAnalysis` that have no cached result
 * 2. POST /tracer/imagine-analysis/ to trigger background LLM analysis
 * 3. Poll GET /tracer/imagine-analysis/ every 3s until complete
 * 4. Cache results in store (also persisted in DB for reload)
 */
export default function useDynamicAnalysis(
  widgets,
  traceData,
  _chatRef,
  traceId,
  projectId,
) {
  const triggeredRef = useRef(new Set());
  const pollIntervalRef = useRef(null);
  const savedViewId = useImagineStore((s) => s._savedViewId);

  // Main trigger effect
  useEffect(() => {
    if (!widgets?.length || !traceId || !traceData) return;

    const store = useImagineStore.getState();

    const needsRun = widgets.filter((w) => {
      if (!w.dynamicAnalysis) return false;
      if (store.getAnalysis(traceId, w.id)) return false;
      if (triggeredRef.current.has(`${traceId}::${w.id}`)) return false;
      return true;
    });

    if (!needsRun.length) {
      // Nothing to trigger — but check if there are pending results in DB to poll
      const hasPending = widgets.some(
        (w) => w.dynamicAnalysis && !store.getAnalysis(traceId, w.id),
      );
      if (hasPending && savedViewId && !pollIntervalRef.current) {
        startPolling(
          traceId,
          savedViewId,
          pollIntervalRef,
          widgets.filter((w) => w.dynamicAnalysis).map((w) => w.id),
        );
      }
      return;
    }

    // Mark as triggered
    needsRun.forEach((w) => triggeredRef.current.add(`${traceId}::${w.id}`));

    const resolvedProjectId = resolveProjectId(projectId, traceData);

    // Trigger analysis via API
    triggerAnalysis(needsRun, traceId, savedViewId, resolvedProjectId).then(
      (started) => {
        if (started) {
          startPolling(
            traceId,
            savedViewId,
            pollIntervalRef,
            needsRun.map((w) => w.id),
          );
        }
      },
    );

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [widgets, traceData, traceId, savedViewId, projectId]);

  const runWidgetAnalysis = useCallback((widget) => {
    if (!widget?.dynamicAnalysis?.prompt || !traceId) return false;

    const store = useImagineStore.getState();
    const currentSavedViewId = store._savedViewId;

    // Clear cache so skeleton shows
    store.setAnalysis(traceId, widget.id, null);
    triggeredRef.current.add(`${traceId}::${widget.id}`);

    const resolvedProjectId = resolveProjectId(projectId, traceData);
    triggerAnalysis(
      [widget],
      traceId,
      currentSavedViewId,
      resolvedProjectId,
    ).then((started) => {
      if (started) {
        startPolling(traceId, currentSavedViewId, pollIntervalRef, [widget.id]);
      }
    });

    return true;
  }, [projectId, traceData, traceId]);

  // Reset when trace changes
  const prevTraceRef = useRef(traceId);
  useEffect(() => {
    if (traceId !== prevTraceRef.current) {
      prevTraceRef.current = traceId;
      triggeredRef.current.clear();
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    }
  }, [traceId]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, []);

  return runWidgetAnalysis;
}

export function resolveProjectId(projectId, traceData) {
  if (projectId) return projectId;

  const trace = traceData?.trace || {};
  const project = traceData?.project || trace?.project || {};
  const inferredProjectId =
    traceData?.project_id ||
    traceData?.projectId ||
    trace?.project_id ||
    trace?.projectId ||
    project?.id;
  if (inferredProjectId) return inferredProjectId;

  const pathParts = window.location.pathname.split("/");
  const observeIdx = pathParts.indexOf("observe");
  return observeIdx >= 0 ? pathParts[observeIdx + 1] : null;
}

function setWidgetFailures(widgets, traceId, message) {
  const store = useImagineStore.getState();
  widgets.forEach((widget) => {
    store.setAnalysis(traceId, widget.id, `*${message}*`);
  });
}

export async function triggerAnalysis(
  widgets,
  traceId,
  savedViewId,
  projectId,
) {
  if (!savedViewId) {
    setWidgetFailures(
      widgets,
      traceId,
      "Analysis needs a saved Imagine view. Save this view and click Rerun to retry.",
    );
    return false;
  }

  try {
    const response = await axios.post(endpoints.imagineAnalysis.trigger, {
      saved_view_id: savedViewId,
      trace_id: traceId,
      ...(projectId ? { project_id: projectId } : {}),
      widgets: widgets.map((w) => ({
        widget_id: w.id,
        prompt: w.dynamicAnalysis.prompt,
      })),
    });

    // If any already completed (cached in DB from previous run), store them
    const analyses = response.data?.result?.analyses || [];
    const store = useImagineStore.getState();
    analyses.forEach((a) => {
      if (a.status === "completed" && a.content) {
        store.setAnalysis(traceId, a.widgetId || a.widget_id, a.content);
      } else if (a.status === "failed") {
        store.setAnalysis(
          traceId,
          a.widgetId || a.widget_id,
          `*Analysis failed: ${a.error || "Unknown error"}. Click Rerun to retry.*`,
        );
      }
    });
    return true;
  } catch (err) {
    setWidgetFailures(
      widgets,
      traceId,
      `Analysis failed: ${err?.response?.data?.error || err?.message || "Unknown error"}. Click Rerun to retry.`,
    );
    // eslint-disable-next-line no-console
    console.error("Failed to trigger analysis:", err);
    return false;
  }
}

function startPolling(
  traceId,
  savedViewId,
  intervalRef,
  expectedWidgetIds = [],
) {
  if (!savedViewId || intervalRef.current) return;

  let failures = 0;
  let emptyPolls = 0;
  const expectedIds = new Set(expectedWidgetIds.filter(Boolean));

  intervalRef.current = setInterval(async () => {
    try {
      const response = await axios.get(endpoints.imagineAnalysis.poll, {
        params: { saved_view_id: savedViewId, trace_id: traceId },
      });

      const analyses = response.data?.result?.analyses || [];
      const store = useImagineStore.getState();

      if (analyses.length === 0) {
        emptyPolls++;
        if (emptyPolls > 10 && expectedIds.size > 0) {
          expectedIds.forEach((widgetId) => {
            store.setAnalysis(
              traceId,
              widgetId,
              "*Analysis failed: no analysis record was created. Click Rerun to retry.*",
            );
          });
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
        return;
      }

      emptyPolls = 0;
      let allDone = true;
      const doneWidgetIds = new Set();
      analyses.forEach((a) => {
        const widgetId = a.widgetId || a.widget_id;
        if (a.status === "completed" && a.content) {
          store.setAnalysis(traceId, widgetId, a.content);
          doneWidgetIds.add(widgetId);
        } else if (a.status === "failed") {
          store.setAnalysis(
            traceId,
            widgetId,
            `*Analysis failed: ${a.error || "Unknown error"}. Click Rerun to retry.*`,
          );
          doneWidgetIds.add(widgetId);
        } else {
          allDone = false;
        }
      });

      if (expectedIds.size > 0) {
        allDone = Array.from(expectedIds).every((id) => doneWidgetIds.has(id));
      }

      if (allDone) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }

      failures = 0;
    } catch {
      failures++;
      if (failures > 10) {
        const store = useImagineStore.getState();
        expectedIds.forEach((widgetId) => {
          store.setAnalysis(
            traceId,
            widgetId,
            "*Analysis failed: polling timed out. Click Rerun to retry.*",
          );
        });
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    }
  }, 3000);
}
