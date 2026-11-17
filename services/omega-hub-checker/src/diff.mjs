/**
 * Step 9, diff (SPEC §4.2 row 9): for an update, what changed since the previous approved version.
 * The diff is for the reviewer only; it never reaches a public surface.
 */
export const DIFF_BUDGET = 200 * 1024;

/** A unified line diff (LCS), bounded; returns null when the files are too big to diff cheaply. */
export function unifiedDiff(before, after, name) {
  const a = before.split("\n");
  const b = after.split("\n");
  if (a.length * b.length > 4_000_000) return `--- ${name}\n+++ ${name}\n@@ too large to diff @@\n`;
  const lcs = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  }
  const out = [`--- ${name} (previous)`, `+++ ${name}`];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      i += 1;
      j += 1;
    } else if (j < b.length && (i >= a.length || lcs[i][j + 1] >= lcs[i + 1][j])) {
      out.push(`+${j + 1}: ${b[j]}`);
      j += 1;
    } else {
      out.push(`-${i + 1}: ${a[i]}`);
      i += 1;
    }
  }
  return `${out.join("\n")}\n`;
}

const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);

export function manifestChanges(before, after) {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])].sort();
  return keys.filter((key) => !same(before?.[key], after?.[key])).map((key) => ({ field: key, before: before?.[key] ?? null, after: after?.[key] ?? null }));
}

/** `{ previous, files: {added, removed, changed}, manifest, highlights, text }` or null for a new entry. */
export function diffVersions(previous, current) {
  if (!previous) return null;
  const added = [...current.files.keys()].filter((rel) => !previous.files.has(rel)).sort();
  const removed = [...previous.files.keys()].filter((rel) => !current.files.has(rel)).sort();
  const changed = [...current.files.keys()].filter((rel) => previous.files.has(rel) && !previous.files.get(rel).equals(current.files.get(rel))).sort();
  const manifest = manifestChanges(previous.manifest, current.manifest);
  const highlights = [];
  const names = (list) => new Set((list ?? []).map((item) => item?.name ?? item?.model ?? item));
  const newKeys = [...names(current.manifest.keys)].filter((key) => !names(previous.manifest.keys).has(key));
  if (newKeys.length) highlights.push(`new keys: ${newKeys.join(", ")}`);
  const newLive = [...names(current.manifest.models?.live)].filter((model) => !names(previous.manifest.models?.live).has(model));
  if (newLive.length) highlights.push(`new live models: ${newLive.join(", ")}`);
  if (!same(previous.manifest.disclosure, current.manifest.disclosure)) highlights.push("the disclosure changed");
  for (const [key, list] of Object.entries(current.observed ?? {})) {
    const fresh = list.filter((item) => !(previous.observed?.[key] ?? []).includes(item));
    if (fresh.length) highlights.push(`new ${key}: ${fresh.slice(0, 5).join(", ")}`);
  }
  const prevRules = new Set((previous.flags ?? []).map((item) => `${item.rule}:${item.file}`));
  const newFlags = (current.flags ?? []).filter((item) => !prevRules.has(`${item.rule}:${item.file}`));
  if (newFlags.length) highlights.push(`new scan flags: ${newFlags.map((item) => `${item.rule} in ${item.file}`).join(", ")}`);
  let budget = DIFF_BUDGET;
  const text = {};
  for (const rel of [...changed, ...added]) {
    const before = previous.files.get(rel)?.toString("utf8") ?? "";
    const chunk = unifiedDiff(before, current.files.get(rel).toString("utf8"), rel);
    if (chunk.length > budget) {
      text[rel] = "(diff budget of 200 KB used up)";
      continue;
    }
    budget -= chunk.length;
    text[rel] = chunk;
  }
  return { previous: previous.version, files: { added, removed, changed }, manifest, highlights, text };
}
