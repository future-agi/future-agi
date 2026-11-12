// Rerun with changes, from anywhere (R4-SPEC D8; frozen API). A run
// menu, `shift+R` or the leaderboard calls openRerunWithChanges; the one
// RerunWithChangesHost in the app shell (lane Ra fills it) reads the
// request with useRerunRequest and opens its dialog.
import { create } from "zustand";

const useRerunStore = create((set) => ({
  request: null,
  open: (req) => set({ request: req ? { ...req, at: Date.now() } : null }),
  close: () => set({ request: null }),
}));

/**
 * Ask the host to open `Rerun with changes…` for a run.
 * @param {{runId: string, scope?: "all"|"failed"|"rows", rowIds?: string[]}} req
 */
export function openRerunWithChanges({ runId, scope, rowIds } = {}) {
  if (!runId) return;
  useRerunStore
    .getState()
    .open({ runId, scope: scope ?? "all", rowIds: rowIds ?? [] });
}

/** `{request, close}`: the open request ({runId, scope, rowIds, at}) or null. */
export function useRerunRequest() {
  const request = useRerunStore((s) => s.request);
  const close = useRerunStore((s) => s.close);
  return { request, close };
}

/** Read without subscribing (tests, non-React callers). */
export const rerunRequestNow = () => useRerunStore.getState().request;
