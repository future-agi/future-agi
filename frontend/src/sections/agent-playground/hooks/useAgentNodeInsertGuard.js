import { useCallback, useRef, useState } from "react";
import { NODE_TYPES } from "../utils/constants";
import useAgentNodeAvailability, {
  AGENT_NODE_AVAILABILITY,
} from "./useAgentNodeAvailability";

/**
 * Guards Agent-node insertion behind eligible-agent availability (TH-4549).
 *
 * The Agent node stays visible everywhere. When the user picks it and no
 * eligible agent is available in the current context (still loading, request
 * failed, or genuinely empty), the insertion is *deferred* instead of adding
 * an unusable node: a setup dialog explains the state and offers a manual
 * refresh. If the refresh finds eligible agents, the original insertion runs.
 *
 * Any other node type passes straight through.
 *
 * @returns {{ guardNodeInsert: (nodeId: string, insert: () => void) => void,
 *            isAgentInsertReady: boolean,
 *            setupDialogProps: object }}
 */
export default function useAgentNodeInsertGuard() {
  const { status, isReady, isRefreshing, refresh } = useAgentNodeAvailability();
  const [dialogOpen, setDialogOpen] = useState(false);
  const pendingInsertRef = useRef(null);

  const closeDialog = useCallback(() => {
    pendingInsertRef.current = null;
    setDialogOpen(false);
  }, []);

  const guardNodeInsert = useCallback(
    (nodeId, insert) => {
      if (nodeId !== NODE_TYPES.AGENT || isReady) {
        insert();
        return;
      }
      pendingInsertRef.current = insert;
      setDialogOpen(true);
    },
    [isReady],
  );

  const handleRefresh = useCallback(async () => {
    const next = await refresh();
    if (next !== AGENT_NODE_AVAILABILITY.READY) return;
    const insert = pendingInsertRef.current;
    closeDialog();
    if (insert) insert();
  }, [refresh, closeDialog]);

  return {
    guardNodeInsert,
    isAgentInsertReady: isReady,
    setupDialogProps: {
      open: dialogOpen,
      status,
      isRefreshing,
      onRefresh: handleRefresh,
      onClose: closeDialog,
    },
  };
}
