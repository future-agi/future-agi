import { useEffect, useRef } from "react";
import axios, { endpoints } from "src/utils/axios";
import useImagineStore from "./useImagineStore";

// A rerun clears the cache before dispatch finishes. Pollers must wait for that
// request, otherwise an early GET can restore the previous completed result.
const dispatches = new Map();
const requestKey = (savedViewId, traceId, widgetId) =>
  `${savedViewId}::${traceId}::${widgetId}`;
const failureContent = "*Analysis failed. Click Rerun to retry.*";

function cacheResults(analyses, widgets, traceId) {
  const store = useImagineStore.getState();
  const ids = new Set(widgets.map((w) => w.id));
  analyses.forEach((analysis) => {
    const id = analysis.widgetId || analysis.widget_id;
    if (!ids.has(id)) return;
    if (analysis.status === "completed" && analysis.content) {
      store.setAnalysis(traceId, id, analysis.content);
    } else if (analysis.status === "failed") {
      store.setAnalysis(traceId, id, failureContent);
    }
  });
}

/**
 * Restore saved results first, then dispatch missing widgets and poll.
 * projectId is the drawer's project; the URL has no project on Users pages.
 */
export default function useDynamicAnalysis(
  widgets,
  traceData,
  _chatRef,
  traceId,
  projectId,
) {
  const savedViewId = useImagineStore((s) => s._savedViewId);
  const analysisCache = useImagineStore((s) => s.analysisCache);
  const startedRef = useRef({ scope: null, ids: new Set() });

  useEffect(() => {
    if (
      !widgets?.length ||
      !traceId ||
      !traceData ||
      !savedViewId ||
      !projectId
    )
      return undefined;
    const scope = `${savedViewId}::${traceId}`;
    if (startedRef.current.scope !== scope) {
      startedRef.current = { scope, ids: new Set() };
    }
    const started = startedRef.current.ids;
    const missing = widgets.filter(
      (w) =>
        w.dynamicAnalysis?.prompt &&
        !useImagineStore.getState().getAnalysis(traceId, w.id),
    );
    if (!missing.length) return undefined;

    let active = true;
    let timer;
    let failures = 0;
    const isCurrent = () =>
      active && useImagineStore.getState()._savedViewId === savedViewId;

    async function poll() {
      try {
        await Promise.all(
          missing.map((w) => {
            const dispatch = dispatches.get(
              requestKey(savedViewId, traceId, w.id),
            );
            // An explicit rerun already supplies the POST for this widget.
            if (dispatch) started.add(w.id);
            return dispatch;
          }),
        );
        if (!isCurrent()) return;
        const response = await axios.get(endpoints.imagineAnalysis.poll, {
          params: { saved_view_id: savedViewId, trace_id: traceId },
        });
        if (!isCurrent()) return;
        const analyses = response.data?.result?.analyses || [];
        cacheResults(analyses, missing, traceId);

        const needsRun = missing.filter((w) => {
          if (
            useImagineStore.getState().getAnalysis(traceId, w.id) ||
            started.has(w.id)
          )
            return false;
          started.add(w.id);
          // GET cannot distinguish stale from active running rows. POST once
          // so the backend can restart stale work or deduplicate an active run.
          return true;
        });
        if (needsRun.length) {
          await triggerAnalysis(
            needsRun,
            traceId,
            savedViewId,
            projectId,
            false,
          );
        }
        // A missing row is not a completed analysis. Keep polling, but report
        // repeated empty responses instead of leaving a permanent skeleton.
        failures = analyses.some((a) =>
          missing.some((w) => w.id === (a.widgetId || a.widget_id)),
        )
          ? 0
          : failures + 1;
      } catch {
        failures += 1;
      }
      if (!isCurrent()) return;
      const unresolved = missing.filter(
        (w) => !useImagineStore.getState().getAnalysis(traceId, w.id),
      );
      if (failures > 10) {
        unresolved.forEach((w) =>
          useImagineStore.getState().setAnalysis(traceId, w.id, failureContent),
        );
      } else if (unresolved.length) {
        timer = setTimeout(poll, 3000);
      }
    }

    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [widgets, traceData, traceId, savedViewId, projectId, analysisCache]);
}

function triggerAnalysis(widgets, traceId, savedViewId, projectId, force) {
  const pending = widgets.filter(
    (w) => !dispatches.has(requestKey(savedViewId, traceId, w.id)),
  );
  if (!pending.length) {
    return Promise.all(
      widgets.map((w) =>
        dispatches.get(requestKey(savedViewId, traceId, w.id)),
      ),
    );
  }
  const keys = pending.map((w) => requestKey(savedViewId, traceId, w.id));
  const request = Promise.resolve().then(async () => {
    try {
      const response = await axios.post(endpoints.imagineAnalysis.trigger, {
        saved_view_id: savedViewId,
        trace_id: traceId,
        project_id: projectId,
        force,
        widgets: pending.map((w) => ({
          widget_id: w.id,
          prompt: w.dynamicAnalysis.prompt,
        })),
      });
      if (useImagineStore.getState()._savedViewId === savedViewId) {
        cacheResults(response.data?.result?.analyses || [], pending, traceId);
      }
    } catch {
      if (useImagineStore.getState()._savedViewId === savedViewId) {
        pending.forEach((w) =>
          useImagineStore.getState().setAnalysis(traceId, w.id, failureContent),
        );
      }
    } finally {
      keys.forEach((key) => dispatches.delete(key));
    }
  });
  keys.forEach((key) => dispatches.set(key, request));
  return request;
}

/** Explicit rerun only; the hook owns WidgetRenderer's initial auto-run. */
export function runAnalysis(widget, traceId, projectId) {
  if (
    !widget?.dynamicAnalysis?.prompt ||
    !traceId ||
    !projectId ||
    widget._needsAnalysis
  )
    return false;
  const store = useImagineStore.getState();
  const savedViewId = store._savedViewId;
  if (!savedViewId) return false;

  // Register dispatch before notifying cache subscribers to restart polling.
  triggerAnalysis([widget], traceId, savedViewId, projectId, true);
  store.setAnalysis(traceId, widget.id, null);
  return true;
}
