export const columnStateToHideMap = (columnState) => {
  const hideMap = {};
  (Array.isArray(columnState) ? columnState : []).forEach((entry) => {
    if (entry && entry.colId) hideMap[entry.colId] = !!entry.hide;
  });
  return hideMap;
};

// Re-stamp the saved view's visibility onto every slot in `columnsObj`,
// skipping ids in `userToggled`. Returns the same object/slot reference when
// nothing changed so React can bail out of re-renders.
export const restampColumns = (
  columnsObj,
  hideMap,
  userToggled = new Set(),
) => {
  if (!columnsObj || !hideMap) return columnsObj;
  let anyChanged = false;
  const next = {};
  Object.keys(columnsObj).forEach((slotKey) => {
    const slot = columnsObj[slotKey] || [];
    let slotChanged = false;
    const updated = slot.map((col) => {
      if (col && col.id in hideMap && !userToggled.has(col.id)) {
        const desiredVisible = !hideMap[col.id];
        if (col.isVisible !== desiredVisible) {
          slotChanged = true;
          return { ...col, isVisible: desiredVisible };
        }
      }
      return col;
    });
    next[slotKey] = slotChanged ? updated : columnsObj[slotKey];
    if (slotChanged) anyChanged = true;
  });
  return anyChanged ? next : columnsObj;
};

// True when a current column's visibility diverges from the saved view's
// columnState. Only compares cols the baseline knows about; ignores custom cols.
export const isColumnVisibilityDirty = (slotColumns, columnState) => {
  if (!Array.isArray(columnState)) return false;
  const baselineVisible = {};
  columnState.forEach((entry) => {
    if (entry && entry.colId) baselineVisible[entry.colId] = !entry.hide;
  });
  return (slotColumns || []).some(
    (col) =>
      col &&
      col.groupBy !== "Custom Columns" &&
      col.id in baselineVisible &&
      (col.isVisible !== false) !== baselineVisible[col.id],
  );
};
