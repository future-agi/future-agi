import { useEffect, useRef, useState } from "react";
import {
  NOTE_DRAFT_DEBOUNCE_MS,
  evictExpiredNoteDrafts,
  noteDraftKey,
  notesSignature,
  readNoteDraft,
  removeNoteDraft,
  sameNotes,
  shouldRestore,
  writeNoteDraft,
} from "./noteDraft";

export { serverNotesOf } from "./noteDraft";

const IDLE = { status: "idle", savedAt: null };
// The panel's own "load the server's notes" effects can land in the same
// commit as a restore and overwrite it; the restore is put back until the
// panel shows it, at most this many times (its load effects settle within
// two commits).
export const NOTE_DRAFT_RESTORE_TRIES = 3;

/**
 * Reviewer notes that save as you type (KH-SCOPE §6 B2).
 *
 * - Writes the item note and per-label notes to this browser 300 ms after
 *   the last keystroke, keyed `fa.review.note.<queueId>.<itemId>.<userId>`.
 * - When the item opens (and whenever the server's notes change), restores a
 *   stored draft that differs from the server's notes and is newer than them
 *   (or the server has none) through `onRestore({itemNotes, labelNotes})`.
 * - Drops the draft once the server holds the same text (after Submit the
 *   detail is read again), and whenever the reviewer types back to it.
 *
 * `server` = {itemNotes, labelNotes, savedAt} (serverNotesOf), or null while
 * the item is still loading; nothing is read or written until it is known.
 * `enabled` = false while the panel shows another item or is read-only.
 *
 * Returns {status: "idle"|"saved"|"restored"|"unavailable", savedAt}.
 */
export function useNoteDraft({
  queueId,
  itemId,
  userId,
  enabled = true,
  itemNotes = "",
  labelNotes = null,
  server = null,
  onRestore,
}) {
  const key = enabled ? noteDraftKey(queueId, itemId, userId) : null;
  const current = { itemNotes, labelNotes };
  const currentSig = notesSignature(current);
  const serverSig = server ? notesSignature(server) : null;
  const serverAt = server?.savedAt ?? null;

  const [state, setState] = useState(IDLE);
  const [restoreTick, setRestoreTick] = useState(0);

  const currentRef = useRef(current);
  currentRef.current = current;
  const currentSigRef = useRef(currentSig);
  currentSigRef.current = currentSig;
  const serverRef = useRef(server);
  serverRef.current = server;
  const onRestoreRef = useRef(onRestore);
  onRestoreRef.current = onRestore;
  const keyRef = useRef(key);
  keyRef.current = key;

  // The key whose restore check has run; the draft waiting to be restored;
  // the text that restore put back (so it is not written again unchanged).
  const checkedKeyRef = useRef(null);
  const pendingRef = useRef(null);
  const restoredSigRef = useRef(null);
  const awaitRef = useRef(null);
  const timerRef = useRef(null);
  const queuedRef = useRef(null);

  // Apply the queued write or removal now.
  const commitRef = useRef(() => {});
  commitRef.current = (report) => {
    const job = queuedRef.current;
    queuedRef.current = null;
    if (!job) return;
    if (job.remove) {
      removeNoteDraft(job.key);
      if (report && job.key === keyRef.current)
        setState((s) => (s.status === "restored" ? s : IDLE));
      return;
    }
    const at = Date.now();
    const kept = writeNoteDraft(job.key, job.notes, at);
    if (report && job.key === keyRef.current)
      setState(
        kept
          ? { status: "saved", savedAt: at }
          : { status: "unavailable", savedAt: null },
      );
  };

  useEffect(() => {
    evictExpiredNoteDrafts();
  }, []);

  // Leaving an item (or the page) keeps what was typed in the last 300 ms.
  useEffect(
    () => () => {
      clearTimeout(timerRef.current);
      commitRef.current(false);
    },
    [key],
  );

  // Restore check: once per item, and again when the server's notes change.
  useEffect(() => {
    if (!key || serverSig == null) {
      checkedKeyRef.current = null;
      pendingRef.current = null;
      awaitRef.current = null;
      return;
    }
    const keyChanged = checkedKeyRef.current !== key;
    checkedKeyRef.current = key;
    if (keyChanged) {
      restoredSigRef.current = null;
      setState(IDLE);
    }
    const draft = readNoteDraft(key);
    if (draft && shouldRestore(draft, serverRef.current)) {
      if (!keyChanged && sameNotes(draft, currentRef.current)) return;
      pendingRef.current = draft;
      setRestoreTick((t) => t + 1);
      return;
    }
    if (draft) removeNoteDraft(key);
  }, [key, serverSig, serverAt]);

  // Restore in the commits after the check, until the panel shows the draft
  // (its own "load the server's notes" effects may overwrite it once).
  useEffect(() => {
    const draft = pendingRef.current;
    if (draft) {
      pendingRef.current = null;
      const sig = notesSignature(draft);
      restoredSigRef.current = sig;
      awaitRef.current = { draft, sig, tries: 0 };
      setState({ status: "restored", savedAt: draft.savedAt });
    }
    const wait = awaitRef.current;
    if (!wait) return;
    if (currentSigRef.current === wait.sig) {
      awaitRef.current = null;
      return;
    }
    if (wait.tries >= NOTE_DRAFT_RESTORE_TRIES) {
      // The panel would not take it: keep the draft, claim nothing.
      awaitRef.current = null;
      restoredSigRef.current = null;
      setState(IDLE);
      return;
    }
    wait.tries += 1;
    onRestoreRef.current?.({
      itemNotes: wait.draft.itemNotes,
      labelNotes: { ...wait.draft.labelNotes },
    });
    setRestoreTick((t) => t + 1);
  }, [restoreTick]);

  // Save as you type (debounced); typing back to the server's text drops it.
  useEffect(() => {
    if (!key || serverSig == null) return undefined;
    if (checkedKeyRef.current !== key || pendingRef.current) return undefined;
    if (awaitRef.current) return undefined;
    if (restoredSigRef.current === currentSig) return undefined;
    restoredSigRef.current = null;
    clearTimeout(timerRef.current);
    queuedRef.current = sameNotes(currentRef.current, serverRef.current)
      ? { key, remove: true }
      : { key, notes: currentRef.current };
    timerRef.current = setTimeout(
      () => commitRef.current(true),
      NOTE_DRAFT_DEBOUNCE_MS,
    );
    // A newer keystroke replaces the queued job. (On leaving the item the
    // [key] effect above, declared first, has already committed it.)
    return () => {
      clearTimeout(timerRef.current);
      queuedRef.current = null;
    };
  }, [key, currentSig, serverSig]);

  return key ? state : IDLE;
}

export default useNoteDraft;
