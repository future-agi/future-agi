import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { toolActionErrorMessage } from "../components/connectorTools";
import { falconAIQueryKeys, updateConnectorTools } from "./useFalconAPI";

// Stable empty list, so a connector with nothing pending does not hand its
// consumer a fresh array on every render.
const NO_NAMES = [];

/**
 * Serialises tool-permission writes.
 *
 * The endpoint replaces the whole `enabled_tool_names` list, so two writes in
 * flight at once is always a lost update: each carries a full list computed
 * before the other existed, and whichever lands last wins. Aborting the loser
 * does not help — the stale list is in the request body, already sent, and the
 * server may apply it regardless of whether we are still listening.
 *
 * So: exactly one request is ever out. Clicks arriving while it is out update
 * the desired set and return; when the request lands the writer checks whether
 * the desired set moved and, if so, sends once more with the final list. Three
 * fast clicks cost two requests and end in the state the user asked for.
 *
 * Because clicks outrun the network, the UI cannot wait for the server —
 * `onApply` reflects the desired set immediately, and a failure re-applies the
 * last set the server accepted.
 *
 * State is keyed by connector: this hook outlives the selection on both
 * surfaces, so a single slot would let a toggle on one connector discard
 * queued work — and roll back the wrong record — on another.
 *
 * @param {object} params
 * @param {Function} params.onApply    (connectorId, names) => void — show this set
 * @param {Function} [params.onDrained] called once the queue empties, after a
 *                                     successful write; for surfaces that want
 *                                     to resync from the server afterwards.
 */
export function useToolPermissionWrites({ onApply, onDrained }) {
  const [toolError, setToolError] = useState(null);
  // { [connectorId]: names } whose change the server has not acknowledged yet.
  const [pending, setPending] = useState({});
  const queryClient = useQueryClient();

  // connectorId -> the set the user wants but the server has not stored.
  const desiredRef = useRef(new Map());
  // connectorId -> the last set the server accepted, to roll back to.
  const confirmedRef = useRef(new Map());
  const writingRef = useRef(false);

  const applyLocally = useCallback(
    (connectorId, names) => {
      queryClient.setQueryData(
        falconAIQueryKeys.connector(connectorId),
        (prev) => (prev ? { ...prev, enabled_tool_names: names } : prev),
      );
      onApply(connectorId, names);
    },
    [queryClient, onApply],
  );

  const clearPending = useCallback((connectorId) => {
    setPending((prev) => {
      if (!(connectorId in prev)) return prev;
      const next = { ...prev };
      delete next[connectorId];
      return next;
    });
  }, []);

  const drain = useCallback(async () => {
    if (writingRef.current) return;
    writingRef.current = true;

    try {
      // Outer loop: onDrained can take as long as a request, and a click
      // during it must not be stranded — anything queued meanwhile is sent.
      while (desiredRef.current.size) {
        while (desiredRef.current.size) {
          const [connectorId, names] = desiredRef.current
            .entries()
            .next().value;
          await updateConnectorTools(connectorId, names);
          confirmedRef.current.set(connectorId, names);

          // Identity comparison is enough: every queued set is a fresh array,
          // so an unchanged reference means nothing arrived while we waited.
          if (desiredRef.current.get(connectorId) === names) {
            desiredRef.current.delete(connectorId);
            clearPending(connectorId);
          }
        }
        setToolError(null);
        if (onDrained) await onDrained();
      }
    } catch (error) {
      setToolError(
        toolActionErrorMessage(error, "Failed to update tool permissions."),
      );
      // The failed write and anything queued behind it are abandoned. Showing
      // them as saved would be a lie, so every connector with unsent work goes
      // back to the set the server last accepted.
      desiredRef.current.forEach((_, connectorId) => {
        const confirmed = confirmedRef.current.get(connectorId);
        if (confirmed) applyLocally(connectorId, confirmed);
        clearPending(connectorId);
      });
      desiredRef.current.clear();
    } finally {
      writingRef.current = false;
    }
  }, [applyLocally, clearPending, onDrained]);

  /**
   * @param {string|number} connectorId
   * @param {string[]} names     the full set the user now wants
   * @param {string[]} touched   names to mark unsaved until the server agrees
   * @param {string[]} baseline  the server-known set, used to seed the rollback
   *                             point at the start of a burst
   */
  const queueWrite = useCallback(
    (connectorId, names, touched, baseline) => {
      // A burst starts whenever this connector has nothing queued: that is the
      // last moment its record on screen is still the server's own answer.
      if (!desiredRef.current.has(connectorId)) {
        confirmedRef.current.set(connectorId, baseline);
      }
      desiredRef.current.set(connectorId, names);
      setPending((prev) => ({
        ...prev,
        [connectorId]: [...new Set([...(prev[connectorId] || []), ...touched])],
      }));
      applyLocally(connectorId, names);
      drain();
    },
    [applyLocally, drain],
  );

  /** The set the user currently wants, which may be ahead of the server. */
  const desiredNames = useCallback(
    (connectorId, fallback) => desiredRef.current.get(connectorId) ?? fallback,
    [],
  );

  const pendingFor = useCallback(
    (connectorId) => pending[connectorId] ?? NO_NAMES,
    [pending],
  );

  return {
    toolError,
    setToolError,
    pendingFor,
    queueWrite,
    desiredNames,
  };
}
