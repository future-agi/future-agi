import { create } from "zustand";

/**
 * Which column `Column from path…` was opened for (the column menu writes,
 * ColumnFromPathDialog — mounted by SchemaDrawer — reads).
 */
export const useColumnFromPathStore = create((set) => ({
  // {datasetId, columnId?, columnName?, path?}: without a column (the
  // `+ Column` / View › Columns entries) the dialog offers every json
  // column; `path` prefills the field (`Add as column` in the row drawer).
  target: null,
  openFor: (target) => set({ target }),
  close: () => set({ target: null }),
}));
