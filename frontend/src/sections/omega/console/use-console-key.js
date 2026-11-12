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
// With requireDoc the bridge supplies a document-scoped AbortSignal. Check it
// before starting and after either outcome: an obsolete mint must not change
// the current document's failure cache, backoff, or viewer admission.
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
 *   getToken: (context?: { signal?: AbortSignal }) => Promise<string | null>,
 *   getTokens: (want: number, context?: { signal?: AbortSignal }) => Promise<string[]>,
 *   mintFailure: string | null,
 *   onStatusChange: (status: string) => void,
 * }}
 */
export function useConsoleKey({
  mintAssertion = defaultMint,
  now = Date.now,
} = {}) {
  const [mintFailure, setMintFailure] = useState(null);
  // { state, until, attempts } after a failed mint, else null.
  const failed = useRef(null);
  const mint = useRef(mintAssertion);
  mint.current = mintAssertion;
  const clock = useRef(now);
  clock.current = now;
  // Signal identity is the bridge's private generation; no nonce reaches here.
  const documentSignal = useRef(null);

  const getTokens = useCallback(async (want = 1, { signal } = {}) => {
    if (signal?.aborted) return [];
    if (signal && signal !== documentSignal.current) {
      documentSignal.current = signal;
      failed.current = null;
      setMintFailure(null);
    }
    const obsolete = () =>
      signal && (signal.aborted || signal !== documentSignal.current);
    const held = failed.current;
    if (held) {
      if (KEPT_UNTIL_RECONNECT.has(held.state)) return [];
      if (clock.current() < held.until) return [];
    }
    try {
      const result = await mint.current({ count: want });
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
      if (obsolete()) return [];
      const state = KNOWN_FAILURES.has(error?.state)
        ? error.state
        : MINT_STATE.UNAVAILABLE;
      const attempts = (held?.attempts ?? 0) + 1;
      const backoff =
        MINT_BACKOFF_MS[Math.min(attempts, MINT_BACKOFF_MS.length) - 1];
      const wait = Number.isFinite(error?.retryAfter)
        ? Math.max(error.retryAfter * 1000, backoff)
        : backoff;
      failed.current = { state, until: clock.current() + wait, attempts };
      setMintFailure(state);
      return [];
    }
  }, []);

  const getToken = useCallback(
    async (context) => (await getTokens(1, context))[0] ?? null,
    [getTokens],
  );

  // EmbeddedSurface reports "connecting" again when the user reconnects. It
  // also goes back to "connecting" when a hello arrives after the no-answer
  // timeout; that is the frame waking up, not a Reconnect, so a failure from
  // that hello's mint stays. Document-scoped callers reset on signal identity
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
