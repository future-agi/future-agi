import { useCallback, useEffect, useRef, useState } from "react";
import {
  createAggregationPollController,
  getAggregationRefreshState,
  getExactAggregationReadState,
} from "src/utils/queryReadState";
import { getExactDashboardResult } from "./widgetUtils";
import { getWidgetPreviewState } from "./widgetEditorState";

export function useWidgetPreviewReads(
  mutateDashboardQuery,
  previewQuerySignature,
) {
  const [lastExactPreview, setLastExactPreview] = useState(null);
  const currentPreviewSignatureRef = useRef("");
  const previewPollTimerRef = useRef(null);
  const previewRequestControllerRef = useRef(null);
  const previewGenerationRef = useRef(0);
  const [isPreviewRefreshing, setIsPreviewRefreshing] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [previewPollingPaused, setPreviewPollingPaused] = useState(false);
  currentPreviewSignatureRef.current = previewQuerySignature;

  useEffect(() => {
    previewGenerationRef.current += 1;
    previewRequestControllerRef.current?.abort();
    previewRequestControllerRef.current = null;
    clearTimeout(previewPollTimerRef.current);
    previewPollTimerRef.current = null;
    setIsPreviewRefreshing(false);
    setPreviewFailed(false);
    setPreviewPollingPaused(false);
  }, [previewQuerySignature]);

  useEffect(
    () => () => {
      previewGenerationRef.current += 1;
      previewRequestControllerRef.current?.abort();
      previewRequestControllerRef.current = null;
      clearTimeout(previewPollTimerRef.current);
    },
    [],
  );

  const runPreviewQuery = useCallback(
    (queryConfig, { refresh = false } = {}) => {
      const signature = JSON.stringify(queryConfig);
      const generation = previewGenerationRef.current + 1;
      previewGenerationRef.current = generation;
      clearTimeout(previewPollTimerRef.current);
      previewPollTimerRef.current = null;
      const pollingController = createAggregationPollController();
      // Polling pending snapshots has its own budget. Exact HTTP reads stay
      // active until completion, replacement, or unmount.
      pollingController.start();
      pollingController.recordAttempt();
      let refreshWasQueued = false;
      setPreviewFailed(false);
      setPreviewPollingPaused(false);

      const isCurrent = () =>
        previewGenerationRef.current === generation &&
        currentPreviewSignatureRef.current === signature;

      const schedulePoll = () => {
        if (!isCurrent() || previewPollTimerRef.current !== null) return;
        pollingController.start();
        const delay = pollingController.nextDelay();
        if (delay === false) {
          const paused =
            pollingController.getTerminationReason() === "poll_budget";
          setIsPreviewRefreshing(false);
          setPreviewPollingPaused(paused);
          setPreviewFailed(!paused);
          return;
        }
        previewPollTimerRef.current = window.setTimeout(() => {
          previewPollTimerRef.current = null;
          pollingController.recordAttempt();
          execute(false);
        }, delay);
      };

      const execute = (forceRefresh) => {
        previewRequestControllerRef.current?.abort();
        const requestController = new AbortController();
        previewRequestControllerRef.current = requestController;
        const finishAttempt = () => {
          if (previewRequestControllerRef.current === requestController) {
            previewRequestControllerRef.current = null;
          }
        };
        mutateDashboardQuery(
          {
            queryConfig,
            refresh: forceRefresh,
            signal: requestController.signal,
          },
          {
            onSuccess: (response) => {
              finishAttempt();
              if (!isCurrent()) return;

              const exactResult = getExactDashboardResult(response);
              const { isRefreshing, refreshFailed } =
                getAggregationRefreshState(response);
              const readState = getExactAggregationReadState(response);
              const responsePreviewState = getWidgetPreviewState(
                response?.data?.result,
                { isSuccess: true },
              );
              pollingController.recordSuccess();
              if (exactResult) {
                setLastExactPreview({ signature, result: exactResult });
                setPreviewFailed(false);
                setPreviewPollingPaused(false);
              }
              if (
                isRefreshing &&
                !refreshFailed &&
                (exactResult || readState === "pending")
              ) {
                refreshWasQueued = true;
                setIsPreviewRefreshing(true);
                schedulePoll();
                return;
              }
              if (
                !refreshFailed &&
                !exactResult &&
                responsePreviewState === "preparing"
              ) {
                refreshWasQueued = true;
                setIsPreviewRefreshing(true);
                schedulePoll();
                return;
              }
              setIsPreviewRefreshing(false);
              if (
                !exactResult &&
                (refreshFailed ||
                  responsePreviewState === "failed" ||
                  readState !== "complete")
              ) {
                setPreviewFailed(true);
              }
            },
            onError: () => {
              finishAttempt();
              if (!isCurrent()) return;
              if (refreshWasQueued && pollingController.recordFailure()) {
                schedulePoll();
                return;
              }
              setIsPreviewRefreshing(false);
              setPreviewFailed(true);
            },
          },
        );
      };

      execute(refresh);
    },
    [mutateDashboardQuery],
  );

  return {
    previewPollingPaused,
    lastExactPreview,
    isPreviewRefreshing,
    previewFailed,
    setPreviewFailed,
    runPreviewQuery,
  };
}
