// Outline (Excel `+ / −`) state for rows, fields, steps (ONE-VIEW-SPEC §3.4).
// Persisted in layout prefs `outline.entries` = [[id, open], …] in
// least-recently-touched order, at most 500 per scope (oldest dropped), so
// explicit choices survive a reload without growing forever.
//
//   const outline = useOutline({ surface: "row", scopeId: datasetId,
//     ids: fieldIds, defaultOpen: (id) => id === "output" });
//   outline.isOpen("input"); outline.toggle("input"); outline.expandAll();
import { useCallback, useMemo } from "react";
import { useLayoutPrefs } from "./layoutPrefs";

export const OUTLINE_MAX = 500;

const entriesOf = (outline) =>
  Array.isArray(outline?.entries)
    ? outline.entries.filter(
        (e) => Array.isArray(e) && typeof e[0] === "string",
      )
    : [];

/**
 * Set ids open/closed; touched ids move to the end; capped (oldest first
 * out). Pure.
 * @param {[string, boolean][]} entries
 * @param {string[]} ids
 * @param {boolean} open
 */
export function setOutline(entries, ids, open, max = OUTLINE_MAX) {
  const touched = new Set(ids);
  const kept = (entries ?? []).filter(([id]) => !touched.has(id));
  const next = [...kept, ...ids.map((id) => [id, Boolean(open)])];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** Open state of one id: stored choice, else the default. Pure. */
export function outlineIsOpen(map, id, defaultOpen) {
  if (map.has(id)) return map.get(id);
  return typeof defaultOpen === "function"
    ? Boolean(defaultOpen(id))
    : Boolean(defaultOpen);
}

/**
 * @param {{surface: string, scopeId?: string, ids?: string[],
 *   defaultOpen?: (id) => boolean | boolean}} args
 * @returns {{isOpen(id): boolean, toggle(id), open(id), close(id),
 *   expandAll(), collapseAll(), allOpen: boolean, anyOpen: boolean}}
 */
export default function useOutline({
  surface,
  scopeId,
  ids = [],
  defaultOpen = () => false,
}) {
  const [prefs, patch] = useLayoutPrefs(surface, scopeId);
  const map = useMemo(() => new Map(entriesOf(prefs.outline)), [prefs.outline]);
  const defRef = defaultOpen;

  const write = useCallback(
    (list, open) =>
      patch((cur) => ({
        ...cur,
        outline: {
          ...(cur.outline ?? {}),
          entries: setOutline(entriesOf(cur.outline), list, open),
        },
      })),
    [patch],
  );

  const isOpen = useCallback(
    (id) => outlineIsOpen(map, id, defRef),
    [map, defRef],
  );
  const open = useCallback((id) => write([id], true), [write]);
  const close = useCallback((id) => write([id], false), [write]);
  const toggle = useCallback(
    (id) => write([id], !outlineIsOpen(map, id, defRef)),
    [write, map, defRef],
  );
  const idsKey = ids.join("|");
  const expandAll = useCallback(
    () => write(ids, true),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [write, idsKey],
  );
  const collapseAll = useCallback(
    () => write(ids, false),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [write, idsKey],
  );
  const states = ids.map((id) => outlineIsOpen(map, id, defRef));
  const allOpen = ids.length > 0 && states.every(Boolean);
  const anyOpen = states.some(Boolean);

  return {
    isOpen,
    toggle,
    open,
    close,
    expandAll,
    collapseAll,
    allOpen,
    anyOpen,
  };
}

export { useOutline };
