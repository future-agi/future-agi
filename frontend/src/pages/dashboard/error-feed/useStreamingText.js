import { useEffect, useRef, useState } from "react";

import { STREAM_CHARS_PER_TICK, STREAM_TICK_MS } from "./constants";

// Per-tab-session memory of which streams have already finished. Keyed by
// a caller-supplied `identityKey` (typically `${message.id}-${slot}`). Once
// a stream completes, its key is recorded here; subsequent mounts (e.g.,
// the user tabbed away and came back) start in instant mode so the
// animation doesn't replay for content they've already seen.
//
// Resets on a hard reload of the page — that's the intended scope. The
// shared analyze thread state lives in the zustand store, but stream
// completion is a UI concern that's specific to the current session.
const STREAMED_KEYS = new Set();

// Reveal `text` one chunk per tick. Returns the visible substring + whether
// more is still incoming so the caller can render a cursor.
//
// Options:
//   - instant: jump straight to the full text (review mode)
//   - identityKey: a stable id for "this particular stream". Once the
//     stream completes, the key is remembered globally so re-mounts skip
//     the animation. Without a key, the stream replays on every mount.
export function useStreamingText(text, options = {}) {
  const { instant = false, identityKey } = options;
  const fullText = typeof text === "string" ? text : "";
  const skipFromMemory = !!identityKey && STREAMED_KEYS.has(identityKey);
  const shouldSkip = instant || skipFromMemory;
  const [revealedLen, setRevealedLen] = useState(
    shouldSkip ? fullText.length : 0,
  );

  // Follow-up answers arrive as text_delta appends, so `fullText` changes on
  // every delta. Resetting to 0 each time restarts the typewriter from empty —
  // the block collapses and regrows continuously until the stream ends. An
  // append is a prefix of the new text, so keep what has already been revealed
  // and only restart when the text is genuinely replaced.
  const prevTextRef = useRef(fullText);
  useEffect(() => {
    const skip = instant || (!!identityKey && STREAMED_KEYS.has(identityKey));
    const isAppend = fullText.startsWith(prevTextRef.current);
    prevTextRef.current = fullText;
    setRevealedLen((prev) => {
      if (skip) return fullText.length;
      return isAppend ? Math.min(prev, fullText.length) : 0;
    });
  }, [fullText, instant, identityKey]);

  useEffect(() => {
    if (revealedLen >= fullText.length) {
      // Stream just hit the end — remember it so we don't replay on remount.
      if (fullText.length > 0 && identityKey) {
        STREAMED_KEYS.add(identityKey);
      }
      return undefined;
    }
    const id = setInterval(() => {
      setRevealedLen((n) =>
        Math.min(n + STREAM_CHARS_PER_TICK, fullText.length),
      );
    }, STREAM_TICK_MS);
    return () => clearInterval(id);
  }, [fullText, revealedLen, identityKey]);

  return {
    revealed: fullText.slice(0, revealedLen),
    isStreaming: revealedLen < fullText.length,
  };
}
