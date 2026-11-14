import { useCallback, useRef, useState } from "react";
import { NODE_TYPES } from "../utils/constants";
import useAgentNodeAvailability, {
  AGENT_NODE_AVAILABILITY,
} from "./useAgentNodeAvailability";

/**
 * Guards Agent-node insertion behind eligible-agent availability (TH-4549).
 *
 * The Agent node stays visible everywhere. When the user picks it and no
 * eligible agent is confirmed for the current context (still loading, request
 * failed/denied, or genuinely empty), the insertion is *deferred* instead of
 * adding an unusable node: a setup dialog explains the state and offers a
 * manual **Refresh agents**. Refreshing only refetches. Once the result is
 * nonempty the dialog offers an explicit **Add Agent node** button which runs
 * the retained insert exactly once (PRD Journey 5, R-02/AC-02, R-08).
 *
 * The retained intent is the caller's *latest* `insert` function, re-read at
 * add time, so the caller re-evaluates mutable context (running workflow,
 * source node still present) when the user actually adds, not when they first
 * clicked (R-13). Callers that need that protection check it inside `insert`.
 *
 * Any other node type passes straight through.
 */
export default function useAgentNodeInsertGuard() {
  const { status, isReady, isRefreshing, refresh } = useAgentNodeAvailability();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const hasPendingRef = useRef(false);
  const latestInsertRef = useRef(null);

  const closeDialog = useCallback(() => {
    hasPendingRef.current = false;
    latestInsertRef.current = null;
    setIsAdding(false);
    setDialogOpen(false);
  }, []);

  /**
   * @param {string} nodeId            node type being inserted
   * @param {() => (void|Promise)} insert  caller's insertion for the
   *        immediate path (eligible agents confirmed) — unchanged behaviour.
   * @param {() => (void|Promise)} [deferredInsert=insert]  variant retained
   *        for the Agent node when insertion is deferred; callers use it to
   *        re-validate their target at add time.
   */
  const guardNodeInsert = useCallback(
    (nodeId, insert, deferredInsert = insert) => {
      if (nodeId !== NODE_TYPES.AGENT || isReady) {
        insert();
        return;
      }
      latestInsertRef.current = deferredInsert;
      hasPendingRef.current = true;
      setDialogOpen(true);
    },
    [isReady],
  );

  // Refresh only refetches eligibility. It never inserts.
  const handleRefresh = useCallback(() => refresh(), [refresh]);

  // Explicit, single-shot add once eligibility is confirmed nonempty.
  const handleAdd = useCallback(async () => {
    if (isAdding || status !== AGENT_NODE_AVAILABILITY.READY) return;
    const insert = hasPendingRef.current ? latestInsertRef.current : null;
    if (!insert) {
      closeDialog();
      return;
    }
    setIsAdding(true);
    try {
      await insert();
    } finally {
      closeDialog();
    }
  }, [isAdding, status, closeDialog]);

  return {
    guardNodeInsert,
    isAgentInsertReady: isReady,
    setupDialogProps: {
      open: dialogOpen,
      status,
      isRefreshing,
      isAdding,
      onRefresh: handleRefresh,
      onAdd: handleAdd,
      onClose: closeDialog,
    },
  };
}
