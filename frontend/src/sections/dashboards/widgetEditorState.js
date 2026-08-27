import { ANALYTICS_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

export const WIDGET_PREVIEW_MAX_WAIT_MS = ANALYTICS_REQUEST_TIMEOUT_MS;

export const getWidgetEditorLoadState = ({
  isEditing,
  isLoading,
  isError,
  dashboard,
  widgetId,
}) => {
  if (isLoading) return "loading";
  if (isError || !dashboard) return "error";
  if (!isEditing) return "ready";
  return dashboard.widgets?.some((widget) => widget.id === widgetId)
    ? "ready"
    : "missing";
};

export const getWidgetPreviewState = (result, mutation) => {
  const queryStatus = result?.queryStatus ?? result?.query_status;
  const queryRefreshing = result?.queryRefreshing ?? result?.query_refreshing;
  const queryRefreshFailed =
    result?.queryRefreshFailed ?? result?.query_refresh_failed;
  const queryComplete = result?.queryComplete ?? result?.query_complete;
  const pending = queryStatus === "pending" || queryRefreshing === true;
  const failed =
    mutation?.isError === true ||
    queryRefreshFailed === true ||
    queryStatus === "degraded" ||
    (queryComplete === false && !pending);

  if (failed) return "failed";
  if (pending) return "preparing";
  if (
    mutation?.isSuccess === true &&
    result &&
    queryComplete !== true &&
    (!Array.isArray(result.metrics) || result.metrics.length === 0)
  ) {
    return "preparing";
  }
  return "ready";
};

export const shouldBlockWidgetPreviewForFailure = ({
  previewFailed,
  hasExactPreview,
}) => Boolean(previewFailed && !hasExactPreview);
