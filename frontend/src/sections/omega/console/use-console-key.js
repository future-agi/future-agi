// The Console frame's keys: fresh Omega session assertions for every hello,
// as many as the hello asks for (CP P7: `want`, minted in one exchange call).
//
// The frame repeats its hello until it gets a key, and asks again whenever its
// pool runs low. A failed mint is kept so those hellos do not each ask the
// backend again:
// - signed out, refused, or a viewer: kept until the user presses Reconnect,
//   because only the user (signing in, a new role) can change the answer;
// - unavailable (no answer, a 5xx, a 429): kept for a short backoff (2 s,
//   doubling to 30 s, or the Retry-After a 429 gave), then the next hello
//   tries again. So a blip of the backend while the Console is open, which
//   has no Reconnect on screen, heals on the Console's next hello.
// The keys themselves are handed straight to the bridge and never kept.
// With requireDoc the bridge supplies a document-scoped AbortSignal and the
// document's identity (an opaque object, the same across a load and the proof
// that resumes the document, new for each document). The failure cache
// belongs to one document: a mint for another document never changes it.
// A load cancels the current signal, so its keys go nowhere; an answer about
// the viewer that still arrives (refused, signed out, a viewer) stands for the
// same document once it is proved again, so a load does not ask the backend a
// second time. An unavailable backend is asked again on the new signal. While
// that document's earlier mint is still in flight, its proof waits for that
// answer instead of asking again.
import { useCallback, useRef, useState } from "react";

import { EMBED_STATUS } from "src/sections/omega/embed";

import { MINT_STATE, createAssertionMinter } from "./session-assertion";

const defaultMint = createAssertionMinter();
const KNOWN_FAILURES = new Set(Object.values(MINT_STATE));
const KEPT_UNTIL_RECONNECT = new Set([
  MINT_STATE.SIGNED_OUT,
  MINT_STATE.REFUSED,
  MINT_STATE.VIEWER,
]);
export const MINT_BACKOFF_MS = Object.freeze([2000, 4000, 8000, 16000, 30000]);
const LATE_HELLO_FROM = new Set([
  EMBED_STATUS.timeout,
  EMBED_STATUS.diagnosing,
]);

/**
 * @param {{
 *   mintAssertion?: (options?: { count?: number }) => Promise<{ token: string, tokens?: string[] }>,
 *   now?: () => number,
 * }} [options]
 * @returns {{
 *   getToken: (context?: { signal?: AbortSignal, document?: object }) => Promise<string | null>,
 *   getTokens: (want: number, context?: { signal?: AbortSignal, document?: object }) => Promise<string[]>,
 *   mintFailure: string | null,
 *   onStatusChange: (status: string) => void,
 * }}
 */
export function useConsoleKey({
  mintAssertion = defaultMint,
  now = Date.now,
} = {}) {
  const [mintFailure, setMintFailure] = useState(null);
  // { state, until, attempts, quiet } after a failed mint, else null; quiet
  // until the page shows it.
  const failed = useRef(null);
  const mint = useRef(mintAssertion);
  mint.current = mintAssertion;
  const clock = useRef(now);
  clock.current = now;
  // The bridge's private identities; no nonce reaches here. The current
  // document is its identity object when the bridge gives one, else its
  // signal; the signal is the latest one for that document.
  const documentIdentity = useRef(null);
  const documentSignal = useRef(null);
  // The mint in flight for one document ({ document, promise }), else null.
  const inFlight = useRef(null);

  const getTokens = useCallback(async (want = 1, context = {}) => {
    // `document` is the bridge's identity for the document asking (owner).
    const { signal, document: owner } = context;
    if (signal?.aborted) return [];
    const identity = owner ?? signal ?? null;
    if (identity && identity !== documentIdentity.current) {
      // A new document: no earlier answer holds for it.
      documentIdentity.current = identity;
      failed.current = null;
      setMintFailure(null);
    } else if (
      signal &&
      signal !== documentSignal.current &&
      failed.current &&
      !KEPT_UNTIL_RECONNECT.has(failed.current.state)
    ) {
      // The same document proved again after a load: an answer about the
      // viewer stands, and an unavailable backend is asked again.
      failed.current = null;
      setMintFailure(null);
    }
    if (signal) documentSignal.current = signal;
    // Another document is current now: this mint's outcome is not its own.
    const otherDocument = () =>
      identity !== null && identity !== documentIdentity.current;
    // The keys go nowhere: the document went away, or a load cancelled them.
    const obsolete = () =>
      otherDocument() ||
      Boolean(signal && (signal.aborted || signal !== documentSignal.current));
    // A kept answer holds this hello. One kept quietly from a cancelled mint
    // (see below) shows on the page once this document asks again.
    const held = () => {
      const failure = failed.current;
      const holds =
        Boolean(failure) &&
        (KEPT_UNTIL_RECONNECT.has(failure.state) ||
          clock.current() < failure.until);
      if (holds && failure.quiet) {
        failure.quiet = false;
        setMintFailure(failure.state);
      }
      return holds;
    };
    if (held()) return [];
    // A proof that resumes this document while its earlier mint is still in
    // flight (the load cancelled only the wait) waits for that answer.
    const pending = inFlight.current;
    if (owner && pending?.document === owner) {
      await pending.promise.catch(() => {});
      if (obsolete() || held()) return [];
    }
    const before = failed.current;
    let attempt;
    try {
      attempt = Promise.resolve(mint.current({ count: want }));
    } catch (error) {
      attempt = Promise.reject(error);
    }
    const entry = owner ? { document: owner, promise: attempt } : null;
    if (entry) inFlight.current = entry;
    try {
      const result = await attempt;
      if (obsolete()) return [];
      const tokens = Array.isArray(result?.tokens)
        ? result.tokens
        : [result?.token];
      if (failed.current) {
        failed.current = null;
        setMintFailure(null);
      }
      return tokens.filter((token) => typeof token === "string" && token);
    } catch (error) {
      if (otherDocument()) return [];
      const state = KNOWN_FAILURES.has(error?.state)
        ? error.state
        : MINT_STATE.UNAVAILABLE;
      // A load cancels the wait, not the answer: an answer about the viewer
      // that arrives anyway is this document's, and stands if it is proved
      // again. It is kept quietly; nothing on the page changes for a
      // cancelled mint. Any other cancelled answer is dropped.
      const cancelled = obsolete();
      if (cancelled && !KEPT_UNTIL_RECONNECT.has(state)) return [];
      const attempts = (before?.attempts ?? 0) + 1;
      const backoff =
        MINT_BACKOFF_MS[Math.min(attempts, MINT_BACKOFF_MS.length) - 1];
      const wait = Number.isFinite(error?.retryAfter)
        ? Math.max(error.retryAfter * 1000, backoff)
        : backoff;
      failed.current = {
        state,
        until: clock.current() + wait,
        attempts,
        quiet: cancelled,
      };
      if (!cancelled) setMintFailure(state);
      return [];
    } finally {
      if (entry && inFlight.current === entry) inFlight.current = null;
    }
  }, []);

  const getToken = useCallback(
    async (context) => (await getTokens(1, context))[0] ?? null,
    [getTokens],
  );

  // EmbeddedSurface reports "connecting" again when the user reconnects. It
  // also goes back to "connecting" when a hello arrives after the no-answer
  // timeout; that is the frame waking up, not a Reconnect, so a failure from
  // that hello's mint stays. Document-scoped callers reset on a new document
  // above, not a delayed React status effect: that effect can arrive AFTER
  // the replacement's own failed mint and must not clear its new refusal.
  const lastStatus = useRef(null);
  const onStatusChange = useCallback((status) => {
    const previous = lastStatus.current;
    lastStatus.current = status;
    if (status !== EMBED_STATUS.connecting || previous === status) return;
    if (LATE_HELLO_FROM.has(previous) || documentSignal.current) return;
    failed.current = null;
    setMintFailure(null);
  }, []);

  return { getToken, getTokens, mintFailure, onStatusChange };
}
