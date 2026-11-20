export const PATCH_BATCH = 25;
export const ROW_PX = 20;

export const STATUS_LABEL = {
  added: "Added",
  removed: "Deleted",
  modified: "Modified",
  renamed: "Renamed",
};

const firstLine = (start, count) => (count ? start : start + 1);

export function hunkRows(hunk) {
  let oldNo = firstLine(hunk.old_start, hunk.old_lines);
  let newNo = firstLine(hunk.new_start, hunk.new_lines);
  return hunk.lines.map((line) => {
    const mark = line[0];
    const text = line.slice(1);
    if (mark === "+") return { type: "add", oldNo: null, newNo: newNo++, text };
    if (mark === "-") return { type: "del", oldNo: oldNo++, newNo: null, text };
    return { type: "context", oldNo: oldNo++, newNo: newNo++, text };
  });
}

export function lineCount(content) {
  if (!content) return 0;
  const lines = content.split("\n");
  return content.endsWith("\n") ? lines.length - 1 : lines.length;
}

export function segments(hunks, totalNewLines = null) {
  const out = [];
  let nextNew = 1;
  let offset = 0;
  hunks.forEach((hunk, index) => {
    const newFirst = firstLine(hunk.new_start, hunk.new_lines);
    const oldFirst = firstLine(hunk.old_start, hunk.old_lines);
    if (newFirst > nextNew) {
      out.push({
        kind: "gap",
        key: `gap-${index}`,
        from: nextNew,
        to: newFirst - 1,
        offset: oldFirst - newFirst,
      });
    }
    out.push({ kind: "hunk", key: `hunk-${index}`, hunk });
    nextNew = newFirst + hunk.new_lines;
    offset = oldFirst + hunk.old_lines - nextNew;
  });
  if (!hunks.length) return out;
  if (totalNewLines == null) {
    out.push({ kind: "gap", key: "gap-end", from: nextNew, to: null, offset });
  } else if (totalNewLines >= nextNew) {
    out.push({
      kind: "gap",
      key: "gap-end",
      from: nextNew,
      to: totalNewLines,
      offset,
    });
  }
  return out;
}

export function gapRows(gap, content) {
  const lines = (content ?? "").split("\n");
  const to = gap.to ?? lineCount(content);
  const rows = [];
  for (let n = gap.from; n <= to; n += 1) {
    rows.push({
      type: "context",
      oldNo: n + gap.offset,
      newNo: n,
      text: lines[n - 1] ?? "",
    });
  }
  return rows;
}

export function splitRows(rows) {
  const out = [];
  let i = 0;
  while (i < rows.length) {
    if (rows[i].type === "context") {
      out.push({ left: rows[i], right: rows[i] });
      i += 1;
    } else {
      const removed = [];
      const added = [];
      while (i < rows.length && rows[i].type === "del") removed.push(rows[i++]);
      while (i < rows.length && rows[i].type === "add") added.push(rows[i++]);
      const pairs = Math.max(removed.length, added.length);
      for (let k = 0; k < pairs; k += 1) {
        out.push({ left: removed[k] ?? null, right: added[k] ?? null });
      }
    }
  }
  return out;
}

export const loadsByItself = (file) => file.kind === "text" && !file.collapsed;

export function planBatches(files, size = PATCH_BATCH) {
  const paths = files.filter(loadsByItself).map((file) => file.path);
  const batches = [];
  const batchOf = {};
  for (let i = 0; i < paths.length; i += size) {
    const batch = paths.slice(i, i + size);
    batch.forEach((path) => {
      batchOf[path] = batches.length;
    });
    batches.push(batch);
  }
  return { batches, batchOf };
}

export function estimatedHeight(file) {
  const changed = (file.additions ?? 0) + (file.deletions ?? 0);
  return Math.min(600, Math.max(3, changed + 6) * ROW_PX);
}

export function olderFirst(versions, a, b) {
  const number = (id) => versions.find((v) => v.id === id)?.number ?? 0;
  return number(a) <= number(b) ? [a, b] : [b, a];
}

export function githubCompareUrl(older, newer) {
  const a = older?.source;
  const b = newer?.source;
  if (a?.kind !== "github" || b?.kind !== "github") return null;
  if (!a.repository || !a.commit_sha || !b.commit_sha) return null;
  if (a.repository.toLowerCase() !== (b.repository || "").toLowerCase()) {
    return null;
  }
  if (a.commit_sha.toLowerCase() === b.commit_sha.toLowerCase()) return null;
  return `https://github.com/${b.repository}/compare/${a.commit_sha}...${b.commit_sha}`;
}

export async function githubFileAnchor(path) {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(path),
  );
  const hex = Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `#diff-${hex}`;
}
