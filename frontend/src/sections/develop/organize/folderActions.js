// Rename, move or remove a virtual folder (W9-SCOPE §5 A6): where the
// folder goes, the per-dataset plan and the words. Pure.
import {
  leafOf,
  parentOf,
  planFolderMove,
  planFolderRemove,
} from "./folderTree";

/** Where folder `path` goes for an action and the typed value. Pure. */
export function folderTarget(action, path, value) {
  const v = String(value ?? "").trim();
  if (action === "rename") {
    const parent = parentOf(path);
    return v.includes("/") ? null : [parent, v].filter(Boolean).join("/");
  }
  if (action === "move")
    return [v.replace(/^\/+|\/+$/g, ""), leafOf(path)]
      .filter(Boolean)
      .join("/");
  return parentOf(path);
}

/** The plan (changes + problem) for an action. Pure. */
export function folderPlan(action, datasets, path, value) {
  if (action === "remove") {
    const { changes } = planFolderRemove(datasets, path);
    return {
      changes,
      problem: changes.length ? null : "This folder is empty.",
    };
  }
  if (action === "rename" && !String(value ?? "").trim())
    return {
      changes: [],
      problem: "A folder name can't be empty or contain “/”.",
    };
  const to = folderTarget(action, path, value);
  if (to === null)
    return {
      changes: [],
      problem: "A folder name can't be empty or contain “/”.",
    };
  const plan = planFolderMove(datasets, path, to);
  if (!plan.problem && !plan.changes.length)
    return {
      changes: [],
      problem: `It is already ${to ? `at ${to}` : "at the top"}.`,
    };
  return plan;
}

const plural = (n) => `${n} dataset${n === 1 ? "" : "s"}`;

/** The toast after a full success. Pure. */
export function doneMessage(action, path, value, n) {
  const to = folderTarget(action, path, value);
  if (action === "rename") return `Renamed ${path} to ${to} · ${plural(n)}`;
  if (action === "move") return `Moved ${path} to ${to} · ${plural(n)}`;
  const parent = parentOf(path);
  return `Removed ${path} · ${plural(n)} moved ${parent ? `to ${parent}` : "out of any folder"}`;
}
