// Response callbacks must not call RowNode.setDataValue: AG Grid 35 treats
// that as another edit while ANY editor is open, or closes the editor if a
// custom event source is supplied. Refreshing display data emits no edit.

/**
 * Show `value` in one cell without an edit: write it into the cell object in
 * the shape the grid's valueSetter uses and repaint that cell. Keeps the
 * cell's metadata. Returns false when there is no cell object to show it in.
 */
export function showCellValue(api, rowNode, columnId, value) {
  const cell = rowNode?.data?.[columnId];
  if (!cell || typeof cell !== "object" || api?.isDestroyed?.()) return false;
  // Inline commits already passed through the valueSetter; dialog saves
  // (JSON Apply Changes, media) can call the writer directly. Preserve both
  // shapes accepted by that setter.
  const valueKey = "cell_value" in cell ? "cell_value" : "cellValue";
  cell[valueKey] = value;
  api?.refreshCells?.({
    rowNodes: [rowNode],
    columns: [columnId],
    force: true,
  });
  return true;
}

const cellValueOf = (cell) =>
  "cell_value" in cell ? cell.cell_value : cell.cellValue;

// The writes to one cell that overlap (sent while an earlier one was still
// pending), keyed by the cell object: the valueSetter edits it in place, and a
// datasource refresh replaces it, which starts a new chain. `before` is what
// the cell held before the chain's first write: the durable value while no
// write of the chain is known to be saved.
const chains = new WeakMap();

const newestNotFailed = (writes) => {
  for (let i = writes.length - 1; i >= 0; i -= 1) {
    if (writes[i].state !== "failed") return writes[i];
  }
  return undefined;
};

/**
 * Track one write to a cell; call `saved(value)` with the value to show once
 * it is acknowledged, or `failed()`. Each response settles the display of the
 * whole chain: the cell shows the newest write that has not failed (its saved
 * value, or while pending what the cell showed when it was committed), and
 * the value before the chain when every write failed. So a late response
 * cannot undo a newer edit, and failed writes never leave an unsaved value.
 * Display only: every commit is still sent, and nothing here writes.
 */
export function cellWriteDisplay(api, rowNode, columnId, oldValue) {
  const cell = rowNode?.data?.[columnId];
  if (!cell || typeof cell !== "object") {
    return { saved: () => {}, failed: () => {} };
  }
  let chain = chains.get(cell);
  if (!chain) {
    chain = { before: oldValue, writes: [] };
    chains.set(cell, chain);
  }
  // An inline commit's valueSetter has already put the new value in the
  // cell; a dialog save has not, so its cell keeps showing what it showed.
  const write = {
    state: "pending",
    shown: cellValueOf(cell),
    value: undefined,
  };
  chain.writes.push(write);

  const settle = (state, value) => {
    if (write.state !== "pending") return;
    // Only the write the cell currently follows can change what it shows.
    const followed = newestNotFailed(chain.writes) === write;
    write.state = state;
    write.value = value;
    if (chain.writes.every((w) => w.state !== "pending")) chains.delete(cell);
    if (!followed || rowNode.data?.[columnId] !== cell) return;
    const next = newestNotFailed(chain.writes);
    const display = !next
      ? chain.before
      : next.state === "saved"
        ? next.value
        : next.shown;
    showCellValue(api, rowNode, columnId, display);
  };

  return {
    saved: (value) => settle("saved", value),
    failed: () => settle("failed"),
  };
}
