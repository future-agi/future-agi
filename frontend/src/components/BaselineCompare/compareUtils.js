import { diffWordsWithSpace } from "diff";

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
