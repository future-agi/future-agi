/**
 * @module @omega/hub/pr-rules
 *
 * Pull request rules, ported from the gallery. They run only when CI names the base; on push and
 * merge_group there is no base and no pull request author, and they are skipped.
 *
 *   P1 one community entry added or changed per pull request
 *   P2 a community entry's `author.github` is the pull request's author (label hub-on-behalf exempts)
 *   P3 `featured` added or changed needs the label hub-featured
 *   P4 `hidden` added or changed needs the label hub-moderation
 *   P5 `author.kind: maintainer` added or changed needs the label hub-maintainer
 */

import { execFileSync } from "node:child_process";
import { HUB_DIR, LABELS, MANIFEST_FILE, finding } from "./schema.mjs";

const same = (left, right) => JSON.stringify(left ?? null) === JSON.stringify(right ?? null);

/**
 * `pr`: `{ changed: [ids added or changed], author: login or "", labels: [names] }`. `now(id)` and
 * `before(id)` return the manifest at HEAD and at the base (null when absent).
 */
export function checkPullRequest(pr, now, before, hubDir = HUB_DIR) {
  const out = [];
  const labels = new Set(pr.labels ?? []);
  const file = (id) => `${hubDir}/${id}/${MANIFEST_FILE}`;
  const changed = [...new Set(pr.changed ?? [])].sort().filter((id) => now(id) !== null);
  const community = changed.filter((id) => now(id)?.author?.kind === "community");
  if (community.length > 1) {
    for (const id of community) out.push(finding("P1", file(id), `this pull request adds or changes ${community.length} community entries (${community.join(", ")})`, "open one pull request per entry"));
  }
  if (pr.author && !labels.has(LABELS.onBehalf)) {
    for (const id of community) {
      const github = String(now(id)?.author?.github ?? "");
      if (github.toLowerCase() !== pr.author.toLowerCase()) {
        out.push(finding("P2", file(id), `\`author.github\` is "${github}" but the pull request is from "${pr.author}"`, `a maintainer opening it for someone else adds the label ${LABELS.onBehalf}`));
      }
    }
  }
  for (const id of changed) {
    const current = now(id);
    const previous = before(id);
    if ("featured" in current && !same(current.featured, previous?.featured) && !labels.has(LABELS.featured)) {
      out.push(finding("P3", file(id), "`featured` is added or changed", `only a maintainer sets it, with the label ${LABELS.featured}`));
    }
    if ("hidden" in current && !same(current.hidden, previous?.hidden) && !labels.has(LABELS.moderation)) {
      out.push(finding("P4", file(id), "`hidden` is added or changed", `only a maintainer sets it, with the label ${LABELS.moderation}`));
    }
    if (current.author?.kind === "maintainer" && previous?.author?.kind !== "maintainer" && !labels.has(LABELS.maintainer)) {
      out.push(finding("P5", file(id), "a maintainer entry is added, or an entry becomes one", `a maintainer adds the label ${LABELS.maintainer}`));
    }
  }
  return out;
}

/** Ids whose folder has a file added, changed or renamed between `base` and HEAD (git). */
export function changedIds(repoRoot, base, hubDir = HUB_DIR) {
  const text = execFileSync("git", ["-C", repoRoot, "diff", "--name-only", "--diff-filter=AMR", `${base}...HEAD`, "--", hubDir], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  const ids = new Set();
  for (const line of text.split("\n")) {
    const parts = line.split("/");
    if (parts.length >= 3 && parts[0] === hubDir && !parts[1].startsWith("_") && !parts[1].startsWith(".")) ids.add(parts[1]);
  }
  return [...ids].sort();
}

/** The manifest of an entry at a git ref, or null. */
export function manifestAt(repoRoot, ref, id, hubDir = HUB_DIR) {
  try {
    const text = execFileSync("git", ["-C", repoRoot, "show", `${ref}:${hubDir}/${id}/${MANIFEST_FILE}`], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
    return JSON.parse(text);
  } catch {
    return null;
  }
}
