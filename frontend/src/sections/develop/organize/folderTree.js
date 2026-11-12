// Virtual dataset folders (W9-SCOPE §5 A6; owner decision 3): a folder is a
// path on the dataset (`support/billing`) and exists while it holds at least
// one dataset. Tree counts, subtree tests, and the per-dataset rewrites that
// rename, move and remove a folder. Pure; the rules and sentences for one
// path are in orgRules.js.
import { FOLDER_MAX_DEPTH, folderProblem, normalizeFolder } from "./orgRules";

export const CYCLE_SENTENCE = "A folder can't move into its own subfolder.";

const segs = (path) => (path ? path.split("/") : []);

/** True when `path` is `folder` or inside it. `""` holds everything. */
export function isUnder(path, folder) {
  const p = normalizeFolder(path);
  const f = normalizeFolder(folder);
  if (!f) return true;
  return p === f || p.startsWith(`${f}/`);
}

/** The parent of a folder (`support/billing` → `support`; top → ""). */
export const parentOf = (path) =>
  segs(normalizeFolder(path)).slice(0, -1).join("/");

/** The last segment (`support/billing` → `billing`). */
export const leafOf = (path) => segs(normalizeFolder(path)).slice(-1)[0] ?? "";

/** Every folder from the top down to `path` (`a/b/c` → a, a/b, a/b/c). */
export function ancestorsOf(path) {
  const s = segs(normalizeFolder(path));
  return s.map((_, i) => s.slice(0, i + 1).join("/"));
}

/**
 * The folders that exist, with own and subtree counts, in path order.
 * @param {string[]} paths  one folder per dataset ("" = no folder)
 * @returns {{path: string, name: string, depth: number, count: number, total: number}[]}
 */
export function folderTree(paths) {
  const nodes = new Map();
  (paths ?? []).forEach((raw) => {
    const path = normalizeFolder(raw);
    if (!path) return;
    ancestorsOf(path).forEach((a) => {
      if (!nodes.has(a))
        nodes.set(a, {
          path: a,
          name: leafOf(a),
          depth: segs(a).length,
          count: 0,
          total: 0,
        });
      nodes.get(a).total += 1;
    });
    nodes.get(path).count += 1;
  });
  return [...nodes.values()].sort((a, b) => a.path.localeCompare(b.path));
}

/** Datasets with no folder. */
export const noFolderCount = (paths) =>
  (paths ?? []).filter((p) => !normalizeFolder(p)).length;

/**
 * Where a dataset in `path` goes when folder `from` becomes `to` (rename or
 * move: a prefix rewrite). Paths outside `from` are unchanged.
 */
export function rewritePrefix(path, from, to) {
  const p = normalizeFolder(path);
  const f = normalizeFolder(from);
  const t = normalizeFolder(to);
  if (!f || !isUnder(p, f)) return p;
  const rest = p.slice(f.length).replace(/^\//, "");
  return [t, rest].filter(Boolean).join("/");
}

/**
 * The problem with moving folder `from` to become `to`, or null. A folder
 * can't move into its own subtree; the result must be a valid path.
 */
export function moveFolderProblem(from, to) {
  const f = normalizeFolder(from);
  const t = normalizeFolder(to);
  if (!f) return "Pick a folder to move.";
  if (t !== f && isUnder(t, f)) return CYCLE_SENTENCE;
  return folderProblem(t);
}

/**
 * The per-dataset folder changes that rename or move folder `from` to `to`,
 * checked against the depth limit for every dataset below it.
 * @param {{id: string, folder: string}[]} datasets
 * @returns {{changes: {id, from, to}[], problem: string|null}}
 */
export function planFolderMove(datasets, from, to) {
  const problem = moveFolderProblem(from, to);
  if (problem) return { changes: [], problem };
  const changes = (datasets ?? [])
    .filter((d) => normalizeFolder(d.folder) && isUnder(d.folder, from))
    .map((d) => ({
      id: d.id,
      from: normalizeFolder(d.folder),
      to: rewritePrefix(d.folder, from, to),
    }))
    .filter((c) => c.from !== c.to);
  const tooDeep = changes.find((c) => segs(c.to).length > FOLDER_MAX_DEPTH);
  if (tooDeep)
    return {
      changes: [],
      problem: `Folders go ${FOLDER_MAX_DEPTH} levels deep at most.`,
    };
  return { changes, problem: null };
}

/**
 * Removing a folder moves its datasets (and its subfolders) up to its
 * parent: `support/billing` → `support`.
 */
export function planFolderRemove(datasets, folder) {
  const f = normalizeFolder(folder);
  const to = parentOf(f);
  const changes = (datasets ?? [])
    .filter((d) => normalizeFolder(d.folder) && isUnder(d.folder, f))
    .map((d) => ({
      id: d.id,
      from: normalizeFolder(d.folder),
      to: rewritePrefix(d.folder, f, to),
    }));
  return { changes, to };
}

/**
 * What Remove folder does, in words: `Its 2 datasets move to support.`;
 * `… move out of any folder.` at the top; subfolders move up one level.
 */
export function removeFolderCopy(datasets, folder) {
  const f = normalizeFolder(folder);
  const { changes, to } = planFolderRemove(datasets, f);
  const own = changes.filter((c) => c.from === f).length;
  const sub = changes.length - own;
  const parts = [];
  if (own) {
    const what = own === 1 ? "Its dataset moves" : `Its ${own} datasets move`;
    parts.push(to ? `${what} to ${to}.` : `${what} out of any folder.`);
  }
  if (sub)
    parts.push(
      `Its subfolders move up one level with their ${sub === 1 ? "dataset" : `${sub} datasets`}.`,
    );
  return parts.join(" ");
}

/** Undo of a set of folder changes: each dataset back where it was. */
export const undoChanges = (changes) =>
  (changes ?? []).map((c) => ({ id: c.id, from: c.to, to: c.from }));

/** The breadcrumb of a folder: `All datasets / support / billing`. */
export function breadcrumbOf(path) {
  return [
    { path: "", label: "All datasets" },
    ...ancestorsOf(path).map((a) => ({ path: a, label: leafOf(a) })),
  ];
}
