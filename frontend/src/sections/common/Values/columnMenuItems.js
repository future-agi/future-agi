// Column menu items for typed values (R2-SPEC P6, L6): `View as ▸ Auto ·
// JSON · YAML · Text` (json and text columns; per dataset + column in
// localStorage, useColumnViewMode) and `Column from path…` (json columns
// and text columns whose values parse as JSON; editors only).
// DataTab/common.js getMainMenuItems spreads the result after the
// `Set role` items; its menuOrder lists both names after `Edit Column Type`.
import {
  COLUMN_VIEW_LABEL,
  COLUMN_VIEW_MODES,
  readColumnViewMode,
  setColumnViewMode,
} from "./useColumnViewMode";
import { isJsonColumn } from "./columnKinds";
import { useColumnFromPathStore } from "./columnFromPathStore";

const ICON_VIEW = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 5c-5 0-9 4.5-10 7 1 2.5 5 7 10 7s9-4.5 10-7c-1-2.5-5-7-10-7Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/></svg>`;
const ICON_PATH = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 4c-2 0-3 1-3 3v2c0 1.5-1 3-2 3 1 0 2 1.5 2 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1.5 1 3 2 3-1 0-2 1.5-2 3v2c0 2-1 3-3 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M10 12h4M12 10v4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;

const VIEW_TYPES = new Set(["json", "text", "conversation", "array"]);

const datasetFromLocation = () => {
  try {
    const m = /\/develop\/([0-9a-f-]{36})/.exec(window.location.pathname);
    return m ? m[1] : undefined;
  } catch {
    return undefined;
  }
};

/**
 * @param {object} column  grid column (`colDef.col`: the wire column with
 *   camelCase aliases)
 * @param {{isViewerRole?: boolean, roleContext?: object}} ctx
 * @returns {Array<{name: string, action?: Function, subMenu?: Array,
 *   icon?: string, disabled?: boolean, tooltip?: string, checked?: boolean}>}
 */
export function datatypeColumnMenuItems(column, ctx = {}) {
  const dataType = column?.dataType ?? column?.data_type;
  const columnId = column?.id;
  if (!columnId) return [];
  const datasetId = ctx.roleContext?.datasetId ?? datasetFromLocation();
  const items = [];
  if (VIEW_TYPES.has(dataType)) {
    const current = readColumnViewMode(datasetId, columnId);
    items.push({
      name: "View as",
      icon: ICON_VIEW,
      subMenu: COLUMN_VIEW_MODES.map((mode) => ({
        name: COLUMN_VIEW_LABEL[mode],
        checked: current === mode,
        action: () => setColumnViewMode(datasetId, columnId, mode),
      })),
    });
  }
  if (isJsonColumn(column)) {
    const viewer = Boolean(ctx.isViewerRole);
    items.push({
      name: "Column from path…",
      icon: ICON_PATH,
      disabled: viewer,
      tooltip: viewer
        ? "Editors can add columns"
        : "A new column from a JSON path",
      action: viewer
        ? undefined
        : () =>
            useColumnFromPathStore.getState().openFor({
              datasetId,
              columnId,
              columnName: column?.name ?? column?.headerName,
            }),
    });
  }
  return items;
}
