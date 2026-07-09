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

// Ordered colIds from a saved columnState (its array order is the column order).
export const columnStateToOrder = (columnState) =>
  (Array.isArray(columnState) ? columnState : [])
    .map((entry) => entry && entry.colId)
    .filter(Boolean);

// Reorder columns to match `order` (colIds); absent ids trail in place. Accepts
// a flat array or a slot-keyed object. Same-ref when unchanged so re-apply can't loop.
export const reorderColumns = (columnsObj, order) => {
  if (!columnsObj || !Array.isArray(order) || order.length === 0)
    return columnsObj;
  const rank = new Map(order.map((id, i) => [id, i]));
  // Reorder one column array; returns the same reference when unchanged.
  const reorderSlot = (slot) => {
    const sorted = slot
      .map((col, i) => ({ col, i }))
      .sort((a, b) => {
        const ra = rank.has(a.col?.id) ? rank.get(a.col.id) : Infinity;
        const rb = rank.has(b.col?.id) ? rank.get(b.col.id) : Infinity;
        return ra !== rb ? ra - rb : a.i - b.i;
      })
      .map((x) => x.col);
    return sorted.some((col, i) => col !== slot[i]) ? sorted : slot;
  };
  if (Array.isArray(columnsObj)) return reorderSlot(columnsObj);
  let anyChanged = false;
  const next = {};
  Object.keys(columnsObj).forEach((slotKey) => {
    const slot = columnsObj[slotKey] || [];
    const sorted = reorderSlot(slot);
    next[slotKey] = sorted !== slot ? sorted : columnsObj[slotKey];
    if (sorted !== slot) anyChanged = true;
  });
  return anyChanged ? next : columnsObj;
};

// Restamp visibility (skipping userToggled) + reorder to the saved order in one
// pass; both derive from the same columnState. Same-ref when nothing changed.
export const applySavedColumns = (
  columnsObj,
  columnState,
  userToggled = new Set(),
) =>
  reorderColumns(
    restampColumns(columnsObj, columnStateToHideMap(columnState), userToggled),
    columnStateToOrder(columnState),
  );

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

// True when the current column order diverges from the saved view's columnState
// order (intersection only). Compares VISIBLE columns only — the user can't
// reorder hidden cols, so a col saved between hidden cols is unreachable by drag
// and a full-order compare would stay dirty forever (TH-6119).
export const isColumnOrderDirty = (slotColumns, columnState) => {
  if (!Array.isArray(columnState)) return false;
  const savedOrder = columnStateToOrder(columnState);
  const currentIds = (slotColumns || [])
    .filter((c) => c?.isVisible !== false)
    .map((c) => c?.id)
    .filter(Boolean);
  const currentSet = new Set(currentIds);
  const savedSet = new Set(savedOrder);
  const currentSeq = currentIds.filter((id) => savedSet.has(id));
  const savedSeq = savedOrder.filter((id) => currentSet.has(id));
  return currentSeq.some((id, i) => id !== savedSeq[i]);
};
