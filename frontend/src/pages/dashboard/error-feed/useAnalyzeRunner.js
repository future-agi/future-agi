import { useCallback, useEffect } from "react";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useErrorFeedStore } from "./store";
import {
  hydrateFromCache,
  prewarmSocket,
  runFollowUp as engineRunFollowUp,
  startRun as engineStartRun,
} from "./clusterAnalyzeSocket";

// Socket engine lives at module scope — runs keep progressing even when
// the Analyze tab is unmounted.

// Shared { token, workspaceId, projectId } trio for both runner hooks so they
// can't drift.
function useRunnerContext(error) {
  const { user } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();
  return {
    token: user?.accessToken,
    workspaceId: currentWorkspaceId,
    projectId: error?.project_id,
  };
}

export function useAnalyzeRunner(clusterId, error) {
  const { token, workspaceId, projectId } = useRunnerContext(error);
  const removeAnalyzePendingStart = useErrorFeedStore(
    (s) => s.removeAnalyzePendingStart,
  );
  const pendingStart = useErrorFeedStore(
    (s) => !!s.analyzePendingStartByCluster[clusterId],
  );
  const hasThread = useErrorFeedStore(
    (s) => !!s.analyzeThreadsByCluster[clusterId],
  );

  // Prewarm socket so first analyze doesn't pay 20-30s cold-start cost.
  useEffect(() => {
    if (token) {
      prewarmSocket({ token, workspaceId });
    }
  }, [token, workspaceId]);

  // Seed from cached synthesis on fresh load (no live thread).
  useEffect(() => {
    if (!clusterId || hasThread) return;
    hydrateFromCache({ clusterId, rca: error?.rca });
  }, [clusterId, hasThread, error?.rca]);

  const startRun = useCallback(() => {
    if (!clusterId) return;
    engineStartRun({ clusterId, projectId, token, workspaceId });
  }, [clusterId, projectId, token, workspaceId]);

  // Auto-fire when pending-start flag flips on.
  useEffect(() => {
    if (!clusterId || !pendingStart) return;
    removeAnalyzePendingStart(clusterId);
    startRun();
  }, [clusterId, pendingStart, removeAnalyzePendingStart, startRun]);

  return { startRun };
}

export function useFollowUpRunner(clusterId, error) {
  const { token, workspaceId, projectId } = useRunnerContext(error);

  const runFollowUp = useCallback(
    (question) => {
      if (!clusterId) return;
      engineRunFollowUp({ clusterId, question, projectId, token, workspaceId });
    },
    [clusterId, projectId, token, workspaceId],
  );

  return { runFollowUp };
}
