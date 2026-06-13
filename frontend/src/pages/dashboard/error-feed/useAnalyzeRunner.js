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

// The Analyze tab is a Falcon conversation embedded in the cluster view. A run
// activates the `/cluster-rca` skill on a fresh Falcon conversation; the
// dedicated cluster-RCA agent streams its investigation back over the socket
// (see clusterAnalyzeSocket.js), then Falcon answers follow-ups on the same
// conversation with the synthesis in context.
//
// These hooks are thin triggers — the socket engine lives at module scope so a
// run keeps progressing (and the headline card keeps updating) even when the
// Analyze tab is unmounted.

export function useAnalyzeRunner(clusterId, error) {
  const { user } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();
  const clearAnalyzePendingStart = useErrorFeedStore(
    (s) => s.clearAnalyzePendingStart,
  );
  const pendingStart = useErrorFeedStore(
    (s) => !!s.analyzePendingStartByCluster[clusterId],
  );
  const hasThread = useErrorFeedStore(
    (s) => !!s.analyzeThreadsByCluster[clusterId],
  );

  // Open the socket as soon as the tab mounts so it's live + heartbeat-warm
  // before the user clicks Analyze — the first chat frame then lands on a ready
  // connection instead of a cold one (which cost 20-30s to first paint).
  useEffect(() => {
    if (user?.accessToken) {
      prewarmSocket({
        token: user.accessToken,
        workspaceId: currentWorkspaceId,
      });
    }
  }, [user?.accessToken, currentWorkspaceId]);

  // Already-analyzed cluster, fresh load (no live thread) → seed from the
  // cached synthesis so the tab shows the result instead of "No analysis yet".
  useEffect(() => {
    if (!clusterId || hasThread) return;
    hydrateFromCache({ clusterId, rca: error?.rca });
  }, [clusterId, hasThread, error?.rca]);

  const startRun = useCallback(() => {
    if (!clusterId) return;
    engineStartRun({
      clusterId,
      projectId: error?.projectId,
      token: user?.accessToken,
      workspaceId: currentWorkspaceId,
    });
  }, [clusterId, error?.projectId, user?.accessToken, currentWorkspaceId]);

  // Auto-fire whenever the pending-start flag flips on for this cluster.
  // Single source of truth: any analyze button anywhere just sets the flag.
  useEffect(() => {
    if (!clusterId || !pendingStart) return;
    clearAnalyzePendingStart(clusterId);
    startRun();
  }, [clusterId, pendingStart, clearAnalyzePendingStart, startRun]);

  return { startRun };
}

export function useFollowUpRunner(clusterId, error) {
  const { user } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();

  const runFollowUp = useCallback(
    (question) => {
      if (!clusterId) return;
      engineRunFollowUp({
        clusterId,
        question,
        projectId: error?.projectId,
        token: user?.accessToken,
        workspaceId: currentWorkspaceId,
      });
    },
    [clusterId, error?.projectId, user?.accessToken, currentWorkspaceId],
  );

  return { runFollowUp };
}
