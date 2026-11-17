// The server's run-source report for the New experiment picker, on the
// shared /api/capabilities/ read. Loading or failed reads offer no Already
// running source (runSourceAvailability.js); a failed read is tried again
// quietly while the picker is open, and never toasts.
import { useEffect, useMemo } from "react";
import { useCapabilities } from "src/hooks/useCapabilities";
import axios from "src/utils/axios";
import {
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  QUIET_RETRY_MS,
  availabilityOfReport,
} from "./runSourceAvailability";

// The workspace the next request names (WorkspaceProvider keeps this header
// in step with the landing, also when it changes without a reload).
const requestWorkspaceId = () =>
  axios?.defaults?.headers?.common?.["X-Workspace-Id"] ?? null;

/**
 * `{status, workspaceId, sources}` for this workspace. `enabled` false reads
 * nothing new (a cached report still answers).
 */
export function useRunSourceAvailability({ enabled = true } = {}) {
  const { data, isError, isFetching, errorUpdateCount, refetch } =
    useCapabilities({ enabled });
  const report = useMemo(
    () => availabilityOfReport(data?.experiment_run_sources),
    [data],
  );
  const workspaceId = requestWorkspaceId();
  // A report read for another workspace is read again once; the next answer
  // is the server's for the workspace the request names, which create meets.
  const otherWorkspace = Boolean(
    report && workspaceId && report.workspaceId !== workspaceId,
  );
  useEffect(() => {
    if (enabled && otherWorkspace) refetch?.({ cancelRefetch: false });
  }, [enabled, otherWorkspace, workspaceId, refetch]);

  const failed = !report && isError;
  useEffect(() => {
    if (!enabled || !failed || isFetching) return undefined;
    const timer = setTimeout(
      () => refetch?.({ cancelRefetch: false }),
      QUIET_RETRY_MS,
    );
    return () => clearTimeout(timer);
  }, [enabled, failed, isFetching, errorUpdateCount, refetch]);

  if (report && !(otherWorkspace && isFetching)) return report;
  if (report || (!failed && data === undefined)) return LOADING_AVAILABILITY;
  return FAILED_AVAILABILITY;
}
