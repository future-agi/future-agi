// The doors into the New experiment drawer (MUST 8): open it on
// a kind's catalog (`src=<card>`) or on one catalog entry (`entry=<id>`),
// the same way the drawer's own ⌘K verbs do (RunExperiment.jsx
// `openPicker`). Prompt and Agent graph add R3's run card instead
// (`initiateCreateMode({firstRunType})`). Unsaved dataset edits first go
// through the pending-changes gate (an experiment runs on a saved version).
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useRunExperimentStore } from "../states";
import { usePendingChangesGate } from "../DataTab/Versions/PendingChangesGate";

/** Cards that add an R3 run card instead of opening a catalog. */
export const R3_CARD_IDS = Object.freeze(["prompt", "agent"]);

/** URL keys for a door (pure; null = remove). */
export function doorParams({ cardId, entryId } = {}) {
  if (entryId) return { new_experiment: "1", entry: entryId, src: null };
  if (cardId && !R3_CARD_IDS.includes(cardId))
    return { new_experiment: "1", src: cardId, entry: null };
  return { new_experiment: "1" };
}

export default function useOpenSource({ prefill } = {}) {
  const [, setSearchParams] = useSearchParams();
  const gate = usePendingChangesGate();
  return useCallback(
    ({ cardId, entryId } = {}) =>
      gate("experiment", () => {
        const store = useRunExperimentStore.getState();
        if (!entryId && R3_CARD_IDS.includes(cardId)) {
          store.initiateCreateMode({ prefill, firstRunType: cardId });
          return;
        }
        const next = new URLSearchParams(window.location.search);
        Object.entries(doorParams({ cardId, entryId })).forEach(([k, v]) =>
          v == null ? next.delete(k) : next.set(k, v),
        );
        setSearchParams(next, { replace: true });
        if (!store.openRunExperiment)
          store.initiateCreateMode({ prefill: prefill ?? {} });
      }),
    [gate, prefill, setSearchParams],
  );
}
