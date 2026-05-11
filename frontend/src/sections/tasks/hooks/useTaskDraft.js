import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Persist an in-progress task create form across reloads.
 *
 * Behavior:
 *  - On first mount, if the URL has no `?draft=<uuid>` query param we
 *    generate a fresh UUID and `replace` the URL so subsequent reloads
 *    land on the same draft.
 *  - The form state is mirrored to localStorage under
 *    `task-draft-<uuid>`. Writes are debounced (300ms) so a fast typist
 *    doesn't hammer localStorage on every keystroke.
 *  - `initialValues` is read synchronously from localStorage on mount,
 *    so the form's `defaultValues` can hydrate from it directly without
 *    an extra `reset()` round-trip.
 *  - On successful task creation, call `clear()` to drop the draft.
 *  - On every save we also evict drafts older than 7 days so localStorage
 *    doesn't accumulate forever.
 *
 * Server-side draft persistence (so drafts survive across devices) is
 * out of scope for v1 — this is intentionally a frontend-only sticky.
 */

const STORAGE_PREFIX = "task-draft-";
const TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

function readDraft(storageKey) {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Stored shape: { savedAt: epoch_ms, values: { ... } }. Older
    // shapes (raw values) are silently ignored.
    if (parsed && typeof parsed === "object" && parsed.values) {
      if (
        typeof parsed.savedAt === "number" &&
        Date.now() - parsed.savedAt > TTL_MS
      ) {
        localStorage.removeItem(storageKey);
        return null;
      }
      const values = parsed.values;
      if (Array.isArray(values.evalsDetails)) {
        values.evalsDetails = values.evalsDetails.filter((e) => e?.id);
      }
      return values;
    }
    return null;
  } catch {
    return null;
  }
}

function writeDraft(storageKey, values) {
  try {
    localStorage.setItem(
      storageKey,
      JSON.stringify({ savedAt: Date.now(), values }),
    );
  } catch {
    // localStorage may be full or disabled (private mode); fail silently
    // — losing the draft is acceptable, breaking the form is not.
  }
}

function evictExpiredDrafts() {
  try {
    const now = Date.now();
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(STORAGE_PREFIX)) continue;
      try {
        const raw = localStorage.getItem(key);
        if (!raw) continue;
        const parsed = JSON.parse(raw);
        if (
          parsed &&
          typeof parsed.savedAt === "number" &&
          now - parsed.savedAt > TTL_MS
        ) {
          localStorage.removeItem(key);
        }
      } catch {
        // skip malformed entries
      }
    }
  } catch {
    // localStorage unavailable
  }
}

export function useTaskDraft() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlDraftId = searchParams.get("draft");

  // Lazy state — only generate a UUID once. Otherwise the URL update
  // would re-trigger the generator and we'd get a new ID on every
  // render until the URL settles.
  const [generatedId] = useState(
    () =>
      urlDraftId ||
      (crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`),
  );
  const draftId = urlDraftId || generatedId;
  const storageKey = `${STORAGE_PREFIX}${draftId}`;

  // Sync the URL to include the draft ID (`replace`, not `push`, so
  // back-button still works as expected).
  useEffect(() => {
    if (!urlDraftId) {
      const next = new URLSearchParams(searchParams);
      next.set("draft", generatedId);
      setSearchParams(next, { replace: true });
    }
    // Take this opportunity to clean up stale drafts.
    evictExpiredDrafts();
  }, [urlDraftId, generatedId, searchParams, setSearchParams]);

  // Read once on mount — synchronous so useForm can hydrate from it
  // via defaultValues without an extra reset() round-trip.
  const initialValues = useMemo(
    () => readDraft(storageKey),
    // Only the storage key matters here. We deliberately read once on
    // mount and ignore later changes — the form is the source of truth
    // after hydration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Debounced save — coalesces rapid form changes (typing) into one
  // localStorage write per 300ms.
  const saveTimer = useRef(null);
  const save = useCallback(
    (values) => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        writeDraft(storageKey, values);
      }, 300);
    },
    [storageKey],
  );

  // Flush any pending save and remove the draft entry. Call this on
  // successful task creation so the draft doesn't linger.
  const clear = useCallback(() => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    try {
      localStorage.removeItem(storageKey);
    } catch {
      // ignore
    }
  }, [storageKey]);

  // Cleanup on unmount — flush pending save so we don't drop the last
  // edit if the user navigates away mid-debounce.
  useEffect(
    () => () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    },
    [],
  );

  return { draftId, initialValues, save, clear };
}
