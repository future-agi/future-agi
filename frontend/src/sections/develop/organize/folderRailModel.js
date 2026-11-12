// The folder rail and breadcrumb as filters (W9-SCOPE §5 A6): a rail click
// sets one filter chip (`Folder is under support/billing`, `Folder is
// empty`, `Starred is true`), so the rail, the chips and saved views are one
// mechanism. Pure.
import { leafOf } from "./folderTree";
import { normalizeFolder } from "./orgRules";

export const FOLDER_FIELD_ID = "folder";
export const STARRED_FIELD_ID = "starred";

/** `folder is under <path>` (subtree), or `folder is empty` for "". */
export function folderCondition(path) {
  const p = normalizeFolder(path ?? "");
  return {
    column_id: FOLDER_FIELD_ID,
    filter_config: p
      ? { filter_type: "text", filter_op: "starts_with", filter_value: p }
      : { filter_type: "text", filter_op: "is_null", filter_value: null },
  };
}

export const STARRED_CONDITION = Object.freeze({
  column_id: STARRED_FIELD_ID,
  filter_config: Object.freeze({
    filter_type: "boolean",
    filter_op: "equals",
    filter_value: true,
  }),
});

const isRailCondition = (c) =>
  c?.column_id === FOLDER_FIELD_ID ||
  (c?.column_id === STARRED_FIELD_ID &&
    c?.filter_config?.filter_value === true);

/**
 * Which rail row the filters select: `{kind: "all"}`, `{kind: "starred"}`,
 * `{kind: "none"}` (no folder) or `{kind: "folder", path}`. A folder
 * condition other than "is under" / "is empty" selects no row (`custom`).
 */
export function railPick(filters) {
  const list = filters ?? [];
  const folder = list.find((c) => c?.column_id === FOLDER_FIELD_ID);
  if (folder) {
    const op = folder.filter_config?.filter_op;
    if (op === "is_null") return { kind: "none" };
    if (op === "starts_with")
      return {
        kind: "folder",
        path: normalizeFolder(String(folder.filter_config.filter_value ?? "")),
      };
    return { kind: "custom" };
  }
  if (list.some(isRailCondition)) return { kind: "starred" };
  return { kind: "all" };
}

/**
 * The filters after a rail click: the other conditions stay; the rail's
 * own (folder, Starred is true) are replaced by the pick's.
 * @param {object[]} filters
 * @param {{kind: "all"|"starred"|"none"|"folder", path?: string}} pick
 */
export function withRailPick(filters, pick) {
  const rest = (filters ?? []).filter((c) => !isRailCondition(c));
  if (pick?.kind === "starred") return [...rest, { ...STARRED_CONDITION }];
  if (pick?.kind === "none") return [...rest, folderCondition("")];
  if (pick?.kind === "folder" && normalizeFolder(pick.path))
    return [...rest, folderCondition(pick.path)];
  return rest;
}

/**
 * The rail's folder rows from the query's `folders` ([{path, count,
 * total}]), in path order, with the leaf name and depth.
 */
export function railRows(folders) {
  return [...(folders ?? [])]
    .map((f) => {
      const path = normalizeFolder(f.path);
      return {
        path,
        name: leafOf(path),
        depth: path ? path.split("/").length : 0,
        count: f.count ?? 0,
        total: f.total ?? 0,
      };
    })
    .filter((r) => r.path)
    .sort((a, b) => a.path.localeCompare(b.path));
}

/**
 * The rail's counts (M4 mount) from a query answer made without the rail's
 * own conditions (folder, Starred is true): All datasets is its total,
 * Starred its `starred` facet, No folder the total less every top-level
 * folder's subtree.
 * @param {{total?: number, facets?: object, folders?: {path, total}[]}} answer
 */
export function railCounts(answer) {
  const allCount = answer?.total ?? 0;
  const starredCount =
    (answer?.facets?.starred ?? []).find((f) => f?.value === true)?.count ?? 0;
  const inFolders = (answer?.folders ?? [])
    .filter((f) => {
      const p = normalizeFolder(f?.path ?? "");
      return p && !p.includes("/");
    })
    .reduce((n, f) => n + (f.total ?? 0), 0);
  return {
    allCount,
    starredCount,
    noFolderCount: Math.max(0, allCount - inFolders),
  };
}
