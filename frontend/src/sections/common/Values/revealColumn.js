// Scroll a grid column into view and flash it once it exists (IT-R1 G5 P8):
// a column added from a path lands at the far right of a wide grid, where
// nobody sees it. The grid refetches its column defs after the add, so poll
// for the column for a few seconds. `gridApi` is the DevelopDetailContext
// ref (`{current: GridApi}`) or a GridApi.

const apiOf = (gridApi) => gridApi?.current ?? gridApi ?? null;

/** @returns {() => void} cancel */
export function revealGridColumn(
  gridApi,
  colId,
  { tries = 20, every = 250 } = {},
) {
  let n = 0;
  let timer = null;
  const attempt = () => {
    const api = apiOf(gridApi);
    const col = api?.getColumn?.(colId);
    if (col) {
      api.ensureColumnVisible?.(colId, "middle");
      // Flash after the scroll has painted the column's cells.
      setTimeout(() => api.flashCells?.({ columns: [colId] }), 120);
      return;
    }
    n += 1;
    if (n < tries) timer = setTimeout(attempt, every);
  };
  attempt();
  return () => clearTimeout(timer);
}
