import { useEffect, useState } from "react";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useResolveDashboardWorkspace } from "./useDashboards";

/**
 * Detects a cross-workspace dashboard 404 and triggers automatic workspace
 * resolution + switch so the user sees the dashboard instead of "not found".
 *
 * Usage — in any component that fetches a dashboard:
 *
 *   const { isResolving, isSwitching, resolveAttempted } =
 *     useCrossWorkspaceRecovery({ dashboardId, isError, error, isLoading });
 *
 * While `isResolving` or `isSwitching` is true the caller should show a
 * loading indicator with a "Looking for this dashboard…" message — the hard
 * reload happens at the end of the switch, so the loading state must cover
 * both stages to avoid flashing a "not found" message in between.
 */
export function useCrossWorkspaceRecovery({
  dashboardId,
  isError,
  error,
  isLoading,
}) {
  const { switchWorkspace, currentWorkspaceId } = useWorkspace();
  const [resolveAttempted, setResolveAttempted] = useState(false);
  const [isSwitching, setIsSwitching] = useState(false);

  const {
    data: resolvedWorkspace,
    isFetching: isResolving,
    refetch: resolveWorkspace,
  } = useResolveDashboardWorkspace(dashboardId);

  // Reset resolve state when the dashboard ID changes so navigating to a
  // different dashboard URL gives a fresh resolve attempt.
  useEffect(() => {
    setResolveAttempted(false);
    setIsSwitching(false);
  }, [dashboardId]);

  // Only trigger the resolve call when the primary fetch 404s in the current
  // workspace — ignore network errors, 5xx, and successful loads.
  // The Axios error interceptor transforms rejected errors: the status code
  // is at error.statusCode, NOT error.response.status.
  useEffect(() => {
    if (
      !isLoading &&
      isError &&
      error?.statusCode === 404 &&
      !resolveAttempted
    ) {
      setResolveAttempted(true);
      resolveWorkspace();
    }
  }, [
    isLoading,
    isError,
    error,
    resolveAttempted,
    resolveWorkspace,
    dashboardId,
  ]);

  // When the resolve call returns a workspace different from the current one,
  // switch to it and save the current URL so the hard page-reload redirects
  // back here. Guard against switching to the same workspace to avoid a
  // useless hard refresh.  Also guard on resolveAttempted to prevent stale
  // cached data from triggering an incorrect switch on a subsequent visit
  // where no 404 / resolve occurred.
  useEffect(() => {
    if (
      resolveAttempted &&
      resolvedWorkspace?.workspace_id &&
      resolvedWorkspace.workspace_id !== currentWorkspaceId
    ) {
      setIsSwitching(true);
      switchWorkspace(
        resolvedWorkspace.workspace_id,
        currentWorkspaceId,
        window.location.href,
      ).catch(() => {
        // switchWorkspace already surfaced the error via snackbar. Drop back
        // to the not-found state instead of spinning forever; the effect's
        // deps haven't changed, so it won't retry in a loop.
        setIsSwitching(false);
      });
    }
  }, [
    resolveAttempted,
    resolvedWorkspace,
    switchWorkspace,
    currentWorkspaceId,
  ]);

  return { isResolving, isSwitching, resolveAttempted };
}
