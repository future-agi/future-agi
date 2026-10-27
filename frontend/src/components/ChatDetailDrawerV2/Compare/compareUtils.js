// Pure helpers for the chat baseline-vs-replay compare view.
import { diffWordsWithSpace } from "diff";

/**
 * Word-level diff between two strings. Pass `side="A"` to retain only
 * the unchanged + removed parts (baseline column) or `side="B"` for
 * unchanged + added parts (replay column). Adjacent same-type parts
 * are merged so the render walks the smallest possible list.
 */
export const computeDiff = (textA, textB, side = null) => {
  if (!textA && !textB) return [];
  if (!textA) return [{ value: textB, added: true }];
  if (!textB) return [{ value: textA, removed: true }];

  const diff = diffWordsWithSpace(textA, textB);
  if (!side) return diff;

  const targetType = side === "A" ? "removed" : "added";
  const filtered = diff.filter((part) =>
    side === "A" ? !part.added : !part.removed,
  );

  // Merge adjacent same-type parts and stitch whitespace into a flanking
  // diff token of the target type so the highlighted run renders as one
  // contiguous span instead of breaking at every whitespace boundary.
  const merged = [];
  for (let i = 0; i < filtered.length; i++) {
    const current = filtered[i];
    const prev = merged[merged.length - 1];

    if (
      prev &&
      prev.added === current.added &&
      prev.removed === current.removed
    ) {
      prev.value += current.value;
      continue;
    }

    if (
      /^\s+$/.test(current.value) &&
      !current.added &&
      !current.removed &&
      prev?.[targetType]
    ) {
      const nextNonWhitespace = filtered
        .slice(i + 1)
        .find((p) => !/^\s+$/.test(p.value));
      if (nextNonWhitespace?.[targetType]) {
        prev.value += current.value;
        continue;
      }
    }

    merged.push({ ...current });
  }

  return merged;
};

/**
 * Pair baseline + replay turns by index so the side-by-side view can
 * render them in lock-step. Missing turns on either side become `null`.
 *
 * @param {{ conversations: Array<object> }} baselineSession
 * @param {{ conversations: Array<object> }} replayedSession
 */
export const matchConversationsByIndex = (baselineSession, replayedSession) => {
  const baseline = baselineSession?.conversations || [];
  const replayed = replayedSession?.conversations || [];
  const maxLength = Math.max(baseline.length, replayed.length);
  const matched = [];
  for (let i = 0; i < maxLength; i++) {
    matched.push({
      baseline: baseline[i] || null,
      replayed: replayed[i] || null,
    });
  }
  return matched;
};
