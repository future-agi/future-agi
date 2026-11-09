import { filterValueText } from "./gridFilterActions";
import { effectiveSplitIds } from "./dataGridView";
import { canTranslateCell } from "src/sections/common/Collab/fieldCollabModel";
import { isSurfaceShown } from "src/launch/launchSwitch";

// G7 cell context menu of the versioned Data grid. Pure: returns AG Grid menu
// items; every side effect goes through `handlers`.
//
// handlers: { filterTo(colId, value, colDef), exclude(colId, value),
//   showAllIn(colId, value), openRow(rowId), copyValue(text),
//   copyRowLink(rowId), addToQueue(rowId), toggleSplit(rowId, splitId, on),
//   manageSplits(), and (W4 §9, M6) commentOnRow(rowId), assignRow(rowId),
//   (HO D2) translateCell(rowId, columnId) }
// `collab` = {canAssign, assignReason} (assigning needs edit; commenting is
// view, so it is always offered).

const ICON = (name) =>
  `<span class="iconify-inline" style="display:inline-flex;width:16px;justify-content:center;opacity:.75">${name}</span>`;

const SVG = {
  filter:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M14 12v7.88c.04.3-.06.62-.29.83a.996.996 0 0 1-1.41 0l-2.01-2.01a.99.99 0 0 1-.29-.83V12h-.03L4.21 4.62a1 1 0 0 1 .17-1.4c.19-.14.4-.22.62-.22h14c.22 0 .43.08.62.22a1 1 0 0 1 .17 1.4L14.03 12z"/></svg>',
  exclude:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10s10-4.5 10-10S17.5 2 12 2m0 2c1.85 0 3.55.63 4.9 1.69L5.69 16.9A7.9 7.9 0 0 1 4 12c0-4.42 3.58-8 8-8m0 16c-1.85 0-3.55-.63-4.9-1.69L18.31 7.1A7.9 7.9 0 0 1 20 12c0 4.42-3.58 8-8 8"/></svg>',
  open: '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M14 3v2h3.59l-9.83 9.83l1.41 1.41L19 6.41V10h2V3m-2 16H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2z"/></svg>',
  copy: '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M19 21H8V7h11m0-2H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2m-3-4H4a2 2 0 0 0-2 2v14h2V3h12z"/></svg>',
  link: '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M10.59 13.41c.41.39.41 1.03 0 1.42c-.39.39-1.03.39-1.42 0a5.003 5.003 0 0 1 0-7.07l3.54-3.54a5.003 5.003 0 0 1 7.07 0a5.003 5.003 0 0 1 0 7.07l-1.49 1.49c.01-.82-.12-1.64-.4-2.42l.47-.48a2.98 2.98 0 0 0 0-4.24a2.98 2.98 0 0 0-4.24 0l-3.53 3.53a2.98 2.98 0 0 0 0 4.24m2.82-4.24c.39-.39 1.03-.39 1.42 0a5.003 5.003 0 0 1 0 7.07l-3.54 3.54a5.003 5.003 0 0 1-7.07 0a5.003 5.003 0 0 1 0-7.07l1.49-1.49c-.01.82.12 1.64.4 2.43l-.47.47a2.98 2.98 0 0 0 0 4.24a2.98 2.98 0 0 0 4.24 0l3.53-3.53a2.98 2.98 0 0 0 0-4.24a.973.973 0 0 1 0-1.42"/></svg>',
  comment:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M9 22a1 1 0 0 1-1-1v-3H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6.1l-3.7 3.71c-.2.19-.45.29-.7.29z"/></svg>',
  assign:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M18 16h-2v-1H8v1H6v-1H2v5h20v-5h-4zm2-8h-3V6l-2-2H9L7 6v2H4c-1.1 0-2 .9-2 2v4h4v-2h2v2h8v-2h2v2h4v-4c0-1.1-.9-2-2-2m-5 0H9V6h6z"/></svg>',
  translate:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="m12.87 15.07l-2.54-2.51l.03-.03A17.5 17.5 0 0 0 14.07 6H17V4h-7V2H8v2H1v2h11.17C11.5 7.92 10.44 9.75 9 11.35C8.07 10.32 7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5-5l3.11 3.11zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2zm-2.62 7l1.62-4.33L19.12 17z"/></svg>',
  queue:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M3 4h18v2H3zm0 7h12v2H3zm0 7h12v2H3zm16-3v-3h-2v3h-3v2h3v3h2v-3h3v-2z"/></svg>',
  split:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M5.5 7A1.5 1.5 0 0 1 4 5.5A1.5 1.5 0 0 1 5.5 4A1.5 1.5 0 0 1 7 5.5A1.5 1.5 0 0 1 5.5 7m15.91 4.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.11 0-2 .89-2 2v7c0 .55.22 1.05.59 1.41l8.99 9c.37.36.87.59 1.42.59s1.05-.23 1.41-.59l7-7c.37-.36.59-.86.59-1.41c0-.56-.23-1.06-.59-1.42"/></svg>',
  manage:
    '<svg width="14" height="14" viewBox="0 0 24 24"><path fill="currentColor" d="M12 15.5A3.5 3.5 0 0 1 8.5 12A3.5 3.5 0 0 1 12 8.5a3.5 3.5 0 0 1 3.5 3.5a3.5 3.5 0 0 1-3.5 3.5m7.43-2.53c.04-.32.07-.64.07-.97s-.03-.66-.07-1l2.11-1.63c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.31-.61-.22l-2.49 1c-.52-.39-1.06-.73-1.69-.98l-.37-2.65A.506.506 0 0 0 14 2h-4c-.25 0-.46.18-.5.42l-.37 2.65c-.63.25-1.17.59-1.69.98l-2.49-1c-.22-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64L4.57 11c-.04.34-.07.67-.07 1s.03.65.07.97l-2.11 1.66c-.19.15-.25.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1.01c.52.4 1.06.74 1.69.99l.37 2.65c.04.24.25.42.5.42h4c.25 0 .46-.18.5-.42l.37-2.65c.63-.26 1.17-.59 1.69-.99l2.49 1.01c.22.08.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64z"/></svg>',
};

const isEvalCol = (colDef) => colDef?.originType === "evaluation";
const isJsonCol = (colDef) =>
  colDef?.dataType === "json" || colDef?.dataType === "array";

export function buildCellContextMenu(
  params,
  { splits = [], ops = [], handlers, collab = null },
) {
  const { column, node, value } = params;
  const data = node?.data;
  const colDef = column?.getColDef?.();
  if (!data || data.__pending) return [];
  const rowId = data.row_id;
  const colId = column?.getColId?.();
  const items = [];
  const isDataCol = Boolean(colDef?.col);
  const text = filterValueText(value);
  const hasValue = value !== null && value !== undefined && text !== "";

  if (
    isDataCol &&
    isEvalCol(colDef) &&
    hasValue &&
    colDef?.dataType === "boolean"
  ) {
    items.push({
      name: `Show all ${text} in ${colDef.headerName}`,
      icon: ICON(SVG.filter),
      action: () => handlers.showAllIn(colId, String(value)),
    });
  } else if (isDataCol && hasValue && !isEvalCol(colDef)) {
    items.push({
      name: `Filter to “${text}”`,
      icon: ICON(SVG.filter),
      action: () => handlers.filterTo(colId, value, colDef),
    });
    if (!isJsonCol(colDef)) {
      items.push({
        name: `Exclude “${text}”`,
        icon: ICON(SVG.exclude),
        action: () => handlers.exclude(colId, value),
      });
    }
  } else if (isDataCol && hasValue && isEvalCol(colDef)) {
    items.push({
      name: `Filter to “${text}”`,
      icon: ICON(SVG.filter),
      action: () => handlers.filterTo(colId, value, colDef),
    });
  }
  if (items.length) items.push("separator");

  items.push(
    {
      name: "Open row",
      shortcut: "Enter",
      icon: ICON(SVG.open),
      action: () => handlers.openRow(rowId),
    },
    {
      name: "Copy value",
      icon: ICON(SVG.copy),
      disabled: !hasValue,
      action: () =>
        handlers.copyValue(
          typeof value === "string" ? value : JSON.stringify(value, null, 2),
        ),
    },
    {
      name: "Copy row link",
      icon: ICON(SVG.link),
      action: () => handlers.copyRowLink(rowId),
    },
    {
      name: "Add row to annotation queue…",
      icon: ICON(SVG.queue),
      action: () => handlers.addToQueue(rowId),
    },
  );
  // L6 launch switch: comments, translate and assignments have no dev
  // endpoint (dataset.collab.comments / .translate / .assignments, each its
  // own claim), nor do splits (dataset.splits).
  // W4 §9 (M6): the row's comments (Activity › Comments, caret in the
  // composer) and Assign to… (AssignDialog), from the cell.
  if (handlers.commentOnRow && isSurfaceShown("dataset.collab.comments"))
    items.push({
      name: "Comment on row",
      icon: ICON(SVG.comment),
      action: () => handlers.commentOnRow(rowId),
    });
  // HO D2: the Row view with this field's Translate to… open.
  if (
    handlers.translateCell &&
    isSurfaceShown("dataset.collab.translate") &&
    isDataCol &&
    canTranslateCell(colDef, value)
  )
    items.push({
      name: "Translate cell…",
      icon: ICON(SVG.translate),
      action: () => handlers.translateCell(rowId, colDef?.col?.id ?? colId),
    });
  if (handlers.assignRow && isSurfaceShown("dataset.collab.assignments"))
    items.push({
      name: "Assign row to…",
      icon: ICON(SVG.assign),
      disabled: collab?.canAssign === false,
      tooltip:
        collab?.canAssign === false
          ? collab?.assignReason ?? undefined
          : undefined,
      action: () => handlers.assignRow(rowId),
    });

  if (!isSurfaceShown("dataset.splits")) return items;
  const current = effectiveSplitIds(data.row_meta?.split_ids, ops, rowId);
  items.push({
    name: "Assign split",
    icon: ICON(SVG.split),
    subMenu: [
      ...splits.map((s) => {
        const on = current.includes(s.id);
        return {
          name: s.name,
          checked: on,
          action: () => handlers.toggleSplit(rowId, s.id, !on),
        };
      }),
      ...(splits.length ? ["separator"] : []),
      {
        name: "Manage splits…",
        icon: ICON(SVG.manage),
        action: () => handlers.manageSplits(),
      },
    ],
  });
  return items;
}
