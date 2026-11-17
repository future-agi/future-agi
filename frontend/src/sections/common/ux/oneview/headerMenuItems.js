// The Excel header menu as data (ONE-VIEW-SPEC §3.2). Pure: the same list
// feeds HeaderMenu (MUI), HeaderMenuCell (MUI Table) and AG Grid's own
// column menu (useAgColumnShaping → getMainMenuItems), so a grid never has
// two menus.
//
// Order: Sort ascending · Sort descending · Clear sort — Group by <label> /
// Stop grouping (groupable only) — Freeze / Unfreeze column · Move left ·
// Move right — Hide column — Columns… (C) · Reset columns.

/**
 * @param {object} layout  useColumnLayout result
 * @param {string} columnId
 * @param {{sort?: {id: string, desc: boolean}|null,
 *   onSort?: (next|null) => void, group?: string|null,
 *   onGroup?: (id|null) => void, onColumns?: () => void}} [opts]
 *   no `onSort` → no sort rows; no `onGroup` → no group rows
 * @returns {object[]} items `{id, label, icon, shortcut?, checked?,
 *   disabled?, reason?, run}` and `{divider: true}`
 */
export function headerMenuItems(layout, columnId, opts = {}) {
  const col = layout?.columns?.find((c) => c.id === columnId);
  if (!col) return [];
  const { sort, onSort, group, onGroup, onColumns } = opts;
  const items = [];
  const sortable = col.sortable !== false;
  if (onSort) {
    const mine = sort?.id === columnId;
    const why = sortable ? undefined : `${col.label} can't be sorted`;
    items.push(
      {
        id: "sortAsc",
        label: "Sort ascending",
        icon: "mdi:sort-ascending",
        checked: mine && !sort.desc,
        disabled: !sortable,
        reason: why,
        run: () => onSort({ id: columnId, desc: false }),
      },
      {
        id: "sortDesc",
        label: "Sort descending",
        icon: "mdi:sort-descending",
        checked: mine && Boolean(sort.desc),
        disabled: !sortable,
        reason: why,
        run: () => onSort({ id: columnId, desc: true }),
      },
      {
        id: "clearSort",
        label: "Clear sort",
        icon: "mdi:sort-variant-remove",
        disabled: !mine,
        reason: mine ? undefined : "Not sorted by this column",
        run: () => onSort(null),
      },
      { divider: true },
    );
  }
  if (onGroup && col.groupable) {
    const grouped = group === columnId;
    items.push(
      grouped
        ? {
            id: "stopGroup",
            label: "Stop grouping",
            icon: "mdi:format-list-bulleted",
            run: () => onGroup(null),
          }
        : {
            id: "groupBy",
            label: `Group by ${col.label}`,
            icon: "mdi:format-list-group",
            run: () => onGroup(columnId),
          },
      { divider: true },
    );
  }
  const pinned = col.pinned === "left";
  const left = layout.canMoveBy(columnId, -1);
  const right = layout.canMoveBy(columnId, 1);
  items.push(
    {
      id: "freeze",
      label: pinned ? "Unfreeze column" : "Freeze column",
      icon: pinned ? "mdi:pin-off-outline" : "mdi:pin-outline",
      disabled: !col.pinnable,
      reason: col.pinnable ? undefined : `${col.label} can't be frozen`,
      run: () => layout.pin(columnId, pinned ? null : "left"),
    },
    // IT-R5 G3 (S3): Excel's `Freeze up to here`.
    ...(layout.pinThrough && col.pinnable
      ? [
          {
            id: "freezeThrough",
            label: "Freeze up to here",
            icon: "mdi:table-column-plus-before",
            run: () => layout.pinThrough(columnId),
          },
        ]
      : []),
    {
      id: "moveLeft",
      label: "Move left",
      icon: "mdi:arrow-left",
      disabled: !left.ok,
      reason: left.reason,
      run: () => layout.moveBy(columnId, -1),
    },
    {
      id: "moveRight",
      label: "Move right",
      icon: "mdi:arrow-right",
      disabled: !right.ok,
      reason: right.reason,
      run: () => layout.moveBy(columnId, 1),
    },
    { divider: true },
    {
      id: "hide",
      label: "Hide column",
      icon: "mdi:eye-off-outline",
      disabled: col.locked,
      reason: col.locked ? `${col.label} is always shown` : undefined,
      run: () => layout.hide(columnId),
    },
    { divider: true },
    {
      id: "columns",
      label: "Columns…",
      icon: "mdi:view-column-outline",
      shortcut: "columns",
      run: () => (onColumns ? onColumns() : layout.openMenu?.()),
    },
    {
      id: "resetColumns",
      label: "Reset columns",
      icon: "mdi:restore",
      disabled: layout.isDefault,
      reason: layout.isDefault ? "Columns are already the default" : undefined,
      run: () => layout.reset(),
    },
  );
  return items;
}

/**
 * The same items as AG Grid MenuItemDef (`getMainMenuItems`): `"separator"`
 * strings for dividers; disabled rows keep their reason as the tooltip.
 */
export function toAgMenuItems(items) {
  const out = [];
  (items ?? []).forEach((i) => {
    if (i.divider) {
      if (out.length && out[out.length - 1] !== "separator")
        out.push("separator");
      return;
    }
    out.push({
      name: i.label,
      action: () => i.run?.(),
      disabled: Boolean(i.disabled),
      tooltip: i.disabled ? i.reason : undefined,
      ...(typeof i.checked === "boolean" ? { checked: i.checked } : {}),
      shortcut: i.shortcut === "columns" ? "C" : undefined,
    });
  });
  while (out[out.length - 1] === "separator") out.pop();
  return out;
}

/** AG Grid 35 omits aria-checked for false; commands deliberately have no checked property. */
export function completeAgMenuCheckedState(popup) {
  popup
    .querySelectorAll('[role="menuitemcheckbox"]:not([aria-checked])')
    .forEach((item) => {
      item.setAttribute("aria-checked", "false");
    });
}
