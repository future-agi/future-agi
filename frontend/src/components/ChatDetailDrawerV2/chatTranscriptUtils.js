/**
 * Helpers for shaping chat-simulation transcript turns into the form the
 * shared voice `TranscriptView` expects. Kept out of the component so they
 * can be unit-tested and reused.
 */

// Chat turns store text in `messages` as a list of plain strings (the chat
// serializer emits `list[str]`), so the turn body is `messages[0]` — not
// `messages[0].content` (that's the voice CallTranscript shape).
export const getChatTurnContent = (turn) => turn?.messages?.[0] ?? "";

// Each turn carries a single `created_at` timestamp; parse it once to epoch
// milliseconds. `TranscriptView.enrichTurns` rebases the earliest value to 0,
// so the first turn reads 0:00 and later turns show offsets relative to it.
export const getChatTurnTimestampMs = (turn) => {
  const createdAt = turn?.created_at;
  if (createdAt == null) return null;
  const ms = new Date(createdAt).getTime();
  return Number.isFinite(ms) ? ms : null;
};

// Chat turns have no per-turn duration, so seed each turn's `duration` with
// its word count. This gives `TranscriptView.TalkRatioBar` a non-zero signal
// to render the inline speaker legend (dot + role + share-of-words %).
export const countWords = (text) => {
  if (!text || typeof text !== "string") return 0;
  return text.trim().split(/\s+/).filter(Boolean).length;
};
