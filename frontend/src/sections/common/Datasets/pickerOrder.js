// Dataset pickers (W9-SCOPE §5 A4, A6): the server already orders the
// names list starred first; pickers keep that order and, once any dataset
// is in a folder, show the starred ones first (their own group) and the
// rest under folder headers. Pure.
import { normalizeFolder } from "src/sections/develop/organize/orgRules";

/** A dataset's folder path ("" when it has none). */
export const folderOfDataset = (d) => normalizeFolder(d?.folder ?? "");

/**
 * Folder groups in the order their first dataset appears (so the folders of
 * starred datasets come first), each keeping the server order inside.
 * Datasets without a folder are one group with `folder: ""`. With no
 * folders at all there is one group and no header is needed.
 * @returns {{folder: string, items: object[]}[]}
 */
export function folderGroups(list) {
  const groups = new Map();
  (list ?? []).forEach((d) => {
    const f = folderOfDataset(d);
    if (!groups.has(f)) groups.set(f, { folder: f, items: [] });
    groups.get(f).items.push(d);
  });
  return [...groups.values()];
}

/** True when the picker should show folder headers. */
export const hasFolders = (list) =>
  (list ?? []).some((d) => folderOfDataset(d) !== "");

/** The header of the leading group of starred datasets. */
export const STARRED_HEADER = "Starred";

/**
 * The picker's groups once any dataset is in a folder: the user's starred
 * datasets first (server order, so newest star first), then the rest under
 * folder headers in path order, `No folder` last. Starred datasets lead
 * every picker, folders or not.
 * @returns {{key: string, header: string, starred?: boolean, items: object[]}[]}
 */
export function pickerGroups(list) {
  const all = list ?? [];
  if (!hasFolders(all)) return [{ key: "all", header: null, items: [...all] }];
  const starred = all.filter((d) => d?.starred);
  const rest = all.filter((d) => !d?.starred);
  return [
    ...(starred.length
      ? [
          {
            key: "starred",
            header: STARRED_HEADER,
            starred: true,
            items: starred,
          },
        ]
      : []),
    // Folders in path order (a parent before its subfolders), `No folder`
    // last; the server order inside each.
    ...folderGroups(rest)
      .sort(
        (a, b) =>
          (a.folder === "") - (b.folder === "") ||
          a.folder.localeCompare(b.folder),
      )
      .map((g) => ({
        key: `folder:${g.folder}`,
        header: folderHeader(g.folder),
        items: g.items,
      })),
  ];
}

/** The flat order the picker shows (headers aside), for ↑ ↓ and Enter. */
export const pickerOrder = (list) => pickerGroups(list).flatMap((g) => g.items);

/**
 * What a picker list renders, in order: `{header, key}` rows (only when
 * some dataset is in a folder) and `{dataset, index, inStarred}` rows,
 * `index` being the dataset's place in pickerOrder(list) (for ↑ ↓ and
 * Enter). A starred dataset sits in the Starred group, so its label shows
 * its folder path (`inStarred`).
 */
export function pickerRows(list) {
  if (!hasFolders(list))
    return (list ?? []).map((dataset, index) => ({ dataset, index }));
  let index = 0;
  return pickerGroups(list).flatMap((g) => [
    { header: g.header, key: g.key },
    ...g.items.map((dataset) => ({
      dataset,
      index: index++,
      inStarred: Boolean(g.starred),
    })),
  ]);
}

/**
 * How the picker orders its list, in words: `Recent first` as before;
 * `Starred first, then recent`; `By folder, recent first`; `Starred first,
 * then by folder`.
 */
export function pickerCaption(list) {
  const starred = (list ?? []).some((d) => d?.starred);
  const folders = hasFolders(list);
  if (starred && folders) return "Starred first, then by folder";
  if (starred) return "Starred first, then recent";
  if (folders) return "By folder, recent first";
  return "Recent first";
}

/** A folder header's words. */
export const folderHeader = (folder) => (folder ? folder : "No folder");

/** `☆ support/billing · Support agent - golden set`, for aria labels. */
export function datasetOptionText(d) {
  const folder = folderOfDataset(d);
  return [
    d?.starred ? "Starred" : null,
    folder || null,
    d?.name ?? "Untitled dataset",
  ]
    .filter(Boolean)
    .join(" · ");
}
