// Which Group by groups are collapsed on the dataset list, remembered per
// user and per grouping (W9-SCOPE §5 A3): layout prefs extras of their own
// surface, so `Reset view` (columns) never reopens them.
//   const [collapsed, toggle] = useGroupCollapse("owner")
//   <DataTable groups={…} collapsedGroups={collapsed} onToggleGroup={toggle} />
import { useCallback, useMemo } from "react";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { DATASET_LIST_SURFACE } from "../datasetList";

export const GROUPS_SURFACE = `${DATASET_LIST_SURFACE}Groups`;

/** The collapsed keys of one grouping in a layout. Pure. */
export function collapsedOf(prefs, groupBy) {
  const list = prefs?.extras?.collapsed?.[groupBy];
  return Array.isArray(list) ? list.map(String) : [];
}

/** The layout with `key` collapsed or opened for `groupBy`. Pure. */
export function toggleCollapsed(prefs, groupBy, key) {
  const cur = collapsedOf(prefs, groupBy);
  const k = String(key);
  const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
  const extras = prefs?.extras ?? {};
  return {
    ...prefs,
    extras: {
      ...extras,
      collapsed: { ...(extras.collapsed ?? {}), [groupBy]: next },
    },
  };
}

export default function useGroupCollapse(groupBy) {
  const [prefs, patch] = useLayoutPrefs(GROUPS_SURFACE);
  const key = collapsedOf(prefs, groupBy).join("\u0000");
  const collapsed = useMemo(() => (key ? key.split("\u0000") : []), [key]);
  const toggle = useCallback(
    (groupKey) => {
      if (!groupBy) return;
      patch((p) => toggleCollapsed(p, groupBy, groupKey));
    },
    [groupBy, patch],
  );
  return [collapsed, toggle];
}
