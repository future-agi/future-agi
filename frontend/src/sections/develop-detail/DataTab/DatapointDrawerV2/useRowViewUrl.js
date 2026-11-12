import { useCallback } from "react";
import { useUrlState } from "src/routes/hooks/use-url-state";

// URL state of the dataset Row view (PRODUCT-SPEC §2.3, Dataset · Data):
//   row      dataset row id (= example_id; stable across versions)
//   rowTab   row | runs | annotations | activity, plus (R2, shown by row
//            kind) conversation | trajectory
//   rowView  full (hides the grid)
//   edit     expected (one-shot: open the Expected editor, then drop it)
export const ROW_TABS = [
  "row",
  // R3 (P2, R2D-20): every field as pretty JSON (`rowTab=raw`).
  "raw",
  "runs",
  "annotations",
  "activity",
  "conversation",
  "trajectory",
];

/**
 * Working stub (L0; owned by L1). Frozen API:
 * { rowId, openRow(id, {push}), closeRow(), rowTab, setRowTab, full,
 *   setFull, editField, clearEditField }
 * Opening a row pushes a history entry (Back closes it); J/K pass
 * {push:false} to replace. Closing and tab/size changes replace.
 */
export default function useRowViewUrl() {
  const [rowId, setRowParam, removeRowParam] = useUrlState("row", null);
  const [rowTabParam, setRowTabParam, removeRowTabParam] = useUrlState(
    "rowTab",
    "row",
  );
  const [rowView, setRowViewParam, removeRowViewParam] = useUrlState(
    "rowView",
    null,
  );
  const [editField, , removeEditParam] = useUrlState("edit", null);
  // History and the Row view share the right slot; opening a row closes
  // History (DataTabLayout), otherwise the click would look dead.
  const [, , removeHistoryParam] = useUrlState("history", null);
  // R3 (P2): the Runs tab's view keys go with the row on close.
  const [, , removeRunParam] = useUrlState("run", null);
  const [, , removeRunShowParam] = useUrlState("rshow", null);
  const [, , removeRunSortParam] = useUrlState("rsort", null);
  const [, , removeActivityParam] = useUrlState("ract", null);

  const openRow = useCallback(
    (id, { push = true } = {}) => {
      if (!id) return;
      removeHistoryParam();
      setRowParam(String(id), { replace: !push });
    },
    [setRowParam, removeHistoryParam],
  );

  const closeRow = useCallback(() => {
    removeRowParam();
    removeRowTabParam();
    removeRowViewParam();
    removeEditParam();
    removeRunParam();
    removeRunShowParam();
    removeRunSortParam();
    removeActivityParam();
  }, [
    removeRowParam,
    removeRowTabParam,
    removeRowViewParam,
    removeEditParam,
    removeRunParam,
    removeRunShowParam,
    removeRunSortParam,
    removeActivityParam,
  ]);

  const setRowTab = useCallback(
    (tab) => {
      if (!tab || tab === "row") removeRowTabParam();
      else setRowTabParam(tab);
    },
    [setRowTabParam, removeRowTabParam],
  );

  const setFull = useCallback(
    (next) => {
      if (next) setRowViewParam("full");
      else removeRowViewParam();
    },
    [setRowViewParam, removeRowViewParam],
  );

  return {
    rowId: rowId ? String(rowId) : null,
    openRow,
    closeRow,
    rowTab: ROW_TABS.includes(rowTabParam) ? rowTabParam : "row",
    setRowTab,
    full: rowView === "full",
    setFull,
    editField: editField || null,
    clearEditField: removeEditParam,
  };
}

export { useRowViewUrl };
