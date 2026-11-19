// Line diff for the recovery closure review. Exact text, never a summary: every
// removed and added line is returned so the operator reviews what changes.
const LIMIT = 2000;

export function lineDiff(before, after) {
  const a = before == null ? [] : before.split("\n");
  const b = after == null ? [] : after.split("\n");
  if (a.length > LIMIT || b.length > LIMIT) {
    throw new Error("This file is too large to review as a line diff.");
  }
  // Longest common subsequence table, filled from the end.
  const table = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const lines = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ kind: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      lines.push({ kind: "removed", text: a[i] });
      i += 1;
    } else {
      lines.push({ kind: "added", text: b[j] });
      j += 1;
    }
  }
  for (; i < a.length; i += 1) lines.push({ kind: "removed", text: a[i] });
  for (; j < b.length; j += 1) lines.push({ kind: "added", text: b[j] });
  return lines;
}
