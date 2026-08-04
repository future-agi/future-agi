import { useEffect, useRef } from "react";

const normalizeRowId = (value) =>
  value === undefined || value === null ? "" : String(value);

export const getDrawerRowId = (row) => row?.id ?? row?.rowData?.rowId;

export const getGridRowNode = (gridApi, rowId) => {
  if (!gridApi?.getRowNode || rowId === undefined || rowId === null) {
    return undefined;
  }

  return gridApi.getRowNode(String(rowId));
};

const rowIdsMatch = (left, right) => {
  if (
    left === undefined ||
    left === null ||
    right === undefined ||
    right === null
  ) {
    return false;
  }

  return normalizeRowId(left) === normalizeRowId(right);
};

export const isDrawerNavigationSourceCurrent = ({
  getCurrentDatapoint,
  navigationRequestIdRef,
  requestId,
  sourceRowData,
  sourceRowId,
}) => {
  if (navigationRequestIdRef?.current !== requestId) {
    return false;
  }

  const currentDatapoint = getCurrentDatapoint?.();
  const currentRowId = currentDatapoint?.rowData?.rowId;

  if (sourceRowId !== undefined && sourceRowId !== null) {
    return rowIdsMatch(currentRowId, sourceRowId);
  }

  return currentDatapoint?.rowData === sourceRowData;
};

const getActiveRowNode = (gridApi, rowIdentity) => {
  const rowNode = getGridRowNode(gridApi, rowIdentity);

  if (rowNode) {
    return rowNode;
  }

  if (Number.isInteger(rowIdentity)) {
    return gridApi?.getDisplayedRowAtIndex?.(rowIdentity);
  }

  return undefined;
};

export const getDrawerRowPosition = (rows = [], datapoint) => {
  const currentRowId = datapoint?.rowData?.rowId;

  if (currentRowId !== undefined && currentRowId !== null) {
    return rows.findIndex(
      (row) =>
        normalizeRowId(getDrawerRowId(row)) === normalizeRowId(currentRowId),
    );
  }

  if (
    Number.isInteger(datapoint?.index) &&
    rows[datapoint.index]?.rowData === datapoint?.rowData
  ) {
    return datapoint.index;
  }

  return -1;
};

export const resolveGridRowIndex = (gridApi, row, fallbackIndex) => {
  const rowNode = getGridRowNode(gridApi, getDrawerRowId(row));

  if (Number.isInteger(rowNode?.rowIndex)) {
    return rowNode.rowIndex;
  }

  if (Number.isInteger(row?.rowIndex)) {
    return row.rowIndex;
  }

  return fallbackIndex;
};

const getSortableRowIndex = (row) =>
  Number.isInteger(row?.rowIndex) ? row.rowIndex : Number.MAX_SAFE_INTEGER;

export const getDisplayedDrawerRows = (gridApi) => {
  const rows = [];

  gridApi?.forEachNode?.((node) => {
    if (node.displayed && node.id !== undefined && node.id !== null) {
      rows.push({
        rowData: node.data,
        id: node.id,
        rowIndex: node.rowIndex,
      });
    }
  });

  return rows.sort(
    (left, right) => getSortableRowIndex(left) - getSortableRowIndex(right),
  );
};

export const syncGridRowVisibility = (gridApi, row, fallbackIndex) => {
  const rowNode = getGridRowNode(gridApi, getDrawerRowId(row));
  const rowIndex = resolveGridRowIndex(gridApi, row, fallbackIndex);

  if (rowNode) {
    gridApi?.ensureNodeVisible?.(rowNode);
  } else if (Number.isInteger(rowIndex)) {
    gridApi?.ensureIndexVisible?.(rowIndex);
  }

  return rowIndex;
};

export const redrawActiveRowHighlight = (
  gridApi,
  previousRowIdentity,
  activeRowIdentity,
) => {
  const rowIdentities = Array.from(
    new Set(
      [previousRowIdentity, activeRowIdentity].filter(
        (rowIdentity) => rowIdentity !== undefined && rowIdentity !== null,
      ),
    ),
  );

  if (rowIdentities.length === 0) {
    return;
  }
  const rowNodes = rowIdentities
    .map((rowIdentity) => getActiveRowNode(gridApi, rowIdentity))
    .filter(Boolean);
  const uniqueRowNodes = Array.from(new Set(rowNodes));

  if (uniqueRowNodes.length > 0) {
    gridApi?.redrawRows?.({ rowNodes: uniqueRowNodes });
    return;
  }

  gridApi?.redrawRows?.();
};

export const useActiveRowHighlightRedraw = (
  gridApiRef,
  activeRowIdentity,
  redrawKey,
) => {
  const previousActiveRowIdentityRef = useRef();

  useEffect(() => {
    redrawActiveRowHighlight(
      gridApiRef.current?.api,
      previousActiveRowIdentityRef.current,
      activeRowIdentity,
    );
    previousActiveRowIdentityRef.current = activeRowIdentity;
  }, [activeRowIdentity, gridApiRef, redrawKey]);
};

export const getActiveRowClass = (params, activeDatapoint) => {
  const activeRowId =
    activeDatapoint?.rowData?.rowId ??
    activeDatapoint?.rowId ??
    activeDatapoint?.id;

  if (activeRowId !== undefined && activeRowId !== null) {
    const rowId = params?.data?.rowId ?? params?.node?.id;
    return normalizeRowId(rowId) === normalizeRowId(activeRowId)
      ? "active-row"
      : "";
  }

  const activeRowIndex = Number.isInteger(activeDatapoint)
    ? activeDatapoint
    : activeDatapoint?.index;

  return params?.node?.rowIndex === activeRowIndex ? "active-row" : "";
};

export const withDrawerRowId = (rowData, rowId) => {
  if (!rowData || rowId === undefined || rowId === null) {
    return rowData;
  }

  return {
    ...rowData,
    rowId: rowData.rowId ?? rowId,
  };
};

const getNextEvalOpen = (rowData, evalOpen, allColumns = []) => {
  if (!evalOpen) {
    return undefined;
  }

  const evalMetricId = evalOpen?.evalMetricId;
  const column = allColumns.find((i) => i?.col?.sourceId === evalMetricId);

  return {
    ...evalOpen,
    ...rowData?.[column?.field],
  };
};

const getDrawerRowFromGridNode = (node) => {
  if (!node || node.id === undefined || node.id === null) {
    return null;
  }

  return {
    rowData: node.data,
    id: node.id,
    rowIndex: node.rowIndex,
  };
};

const getDisplayedDrawerRowAtIndex = (gridApi, rowIndex) => {
  if (!gridApi?.getDisplayedRowAtIndex || !Number.isInteger(rowIndex)) {
    return undefined;
  }

  return getDrawerRowFromGridNode(gridApi.getDisplayedRowAtIndex(rowIndex));
};

const getAdjacentDisplayedDrawerRow = (gridApi, datapoint, direction) => {
  if (!gridApi?.getDisplayedRowAtIndex) {
    return undefined;
  }

  const step = direction === "next" ? 1 : -1;
  const currentRowId = datapoint?.rowData?.rowId;
  const currentRowNode = getGridRowNode(gridApi, currentRowId);
  const currentRowIndex = Number.isInteger(currentRowNode?.rowIndex)
    ? currentRowNode.rowIndex
    : datapoint?.index;

  if (!Number.isInteger(currentRowIndex)) {
    return undefined;
  }

  if (currentRowId !== undefined && currentRowId !== null && !currentRowNode) {
    const displayedCurrentRow = getDisplayedDrawerRowAtIndex(
      gridApi,
      currentRowIndex,
    );

    if (!rowIdsMatch(getDrawerRowId(displayedCurrentRow), currentRowId)) {
      return direction === "previous" ? undefined : null;
    }
  }

  return getDisplayedDrawerRowAtIndex(gridApi, currentRowIndex + step);
};

export const canNavigatePreviousDrawerRow = (rows = [], datapoint) => {
  const currentDrawerIndex = getDrawerRowPosition(rows, datapoint);

  return (
    currentDrawerIndex > 0 && Boolean(rows[currentDrawerIndex - 1]?.rowData)
  );
};

export const canNavigatePreviousDrawerRowFromGrid = (
  rows = [],
  datapoint,
  gridApi,
  { allowFullScan = true } = {},
) => {
  if (canNavigatePreviousDrawerRow(rows, datapoint)) {
    return true;
  }

  if (getAdjacentDisplayedDrawerRow(gridApi, datapoint, "previous")?.rowData) {
    return true;
  }

  if (!allowFullScan) {
    return false;
  }

  return canNavigatePreviousDrawerRow(
    getDisplayedDrawerRows(gridApi),
    datapoint,
  );
};

const getAdjacentRowIds = (navigationResult, direction) => {
  const rowIds =
    navigationResult?.[direction]?.rowId ??
    navigationResult?.[direction]?.row_id;

  if (rowIds === undefined || rowIds === null) {
    return [];
  }

  return Array.isArray(rowIds) ? rowIds : [rowIds];
};

const DRAWER_SHORTCUT_TEXT_ENTRY_SELECTOR = [
  "input",
  "select",
  "textarea",
  "[role='combobox']",
  "[role='textbox']",
  "[contenteditable='true']",
].join(", ");

export const shouldIgnoreDrawerNavigationShortcut = (target) => {
  if (!target) {
    return false;
  }

  return Boolean(
    target.isContentEditable ||
      target.getAttribute?.("contenteditable") === "true" ||
      target.closest?.(DRAWER_SHORTCUT_TEXT_ENTRY_SELECTOR),
  );
};

export const handleDrawerNavigationShortcut = (event, state) => {
  if (!state?.enabled) return false;
  if (shouldIgnoreDrawerNavigationShortcut(event.target)) return false;

  const isNext = event.key === "j" || event.key === "J";
  const isPrev = event.key === "k" || event.key === "K";
  if (!isNext && !isPrev) return false;
  if (event.metaKey || event.ctrlKey || event.altKey) return false;

  if (
    isNext &&
    !state.navLoading &&
    state.datapointIndex !== state.totalRowCount - 1
  ) {
    event.preventDefault();
    event.stopPropagation();
    state.onNavigate("next");
    return true;
  }

  if (isPrev && !state.navLoading && state.canNavigatePrevious) {
    event.preventDefault();
    event.stopPropagation();
    state.onNavigate("previous");
    return true;
  }

  return false;
};

const isNavigationAbortError = (error) =>
  error?.name === "AbortError" ||
  error?.name === "CanceledError" ||
  error?.code === "ERR_CANCELED";

// Navigation is intentionally resolved in this order:
// live displayed grid row -> cached hydrated row -> cached placeholder hydration
// -> next-boundary fetch. Previous navigation stays cache/live-grid bounded.
export const navigateDrawerRows = async ({
  allColumns = [],
  datapoint,
  direction,
  evalOpen,
  getCellData,
  getNextItemIds,
  getNextRowsRequestParams = () => ({}),
  gridApi,
  isNavigationCurrent = () => true,
  logger,
  onNavigationLoadError,
  rows = [],
  setDatapoint,
  setEvalOpen,
  setRows,
}) => {
  let activeRows = rows;
  let rowsWereResynced = false;
  let displayedRowsSnapshot;
  const step = direction === "next" ? 1 : -1;
  let currentDrawerIndex = getDrawerRowPosition(activeRows, datapoint);

  const getDisplayedRowsSnapshot = () => {
    if (!displayedRowsSnapshot) {
      displayedRowsSnapshot = getDisplayedDrawerRows(gridApi);
    }

    return displayedRowsSnapshot;
  };

  const resyncDisplayedRows = ({ requireAdjacentRow = false } = {}) => {
    const displayedRows = getDisplayedRowsSnapshot();
    const displayedDrawerIndex = getDrawerRowPosition(displayedRows, datapoint);

    if (displayedDrawerIndex === -1) {
      return false;
    }

    if (
      requireAdjacentRow &&
      !displayedRows[displayedDrawerIndex + step]?.rowData
    ) {
      return false;
    }

    activeRows = displayedRows;
    currentDrawerIndex = displayedDrawerIndex;
    rowsWereResynced = true;
    return true;
  };

  if (currentDrawerIndex === -1) {
    resyncDisplayedRows();
  }

  if (currentDrawerIndex === -1) {
    return false;
  }

  let targetDrawerIndex = currentDrawerIndex + step;
  let adjacentDisplayedRow = getAdjacentDisplayedDrawerRow(
    gridApi,
    datapoint,
    direction,
  );

  if (
    (adjacentDisplayedRow === undefined || adjacentDisplayedRow === null) &&
    gridApi?.forEachNode
  ) {
    const displayedRows = getDisplayedRowsSnapshot();
    const displayedDrawerIndex = getDrawerRowPosition(displayedRows, datapoint);

    if (displayedDrawerIndex !== -1) {
      adjacentDisplayedRow = displayedRows[displayedDrawerIndex + step] ?? null;

      if (adjacentDisplayedRow?.rowData) {
        activeRows = displayedRows;
        currentDrawerIndex = displayedDrawerIndex;
        targetDrawerIndex = currentDrawerIndex + step;
        rowsWereResynced = true;
      }
    }
  }

  if (
    adjacentDisplayedRow === undefined &&
    !activeRows[targetDrawerIndex]?.rowData
  ) {
    resyncDisplayedRows({ requireAdjacentRow: true });
    targetDrawerIndex = currentDrawerIndex + step;
    adjacentDisplayedRow = activeRows[targetDrawerIndex] ?? null;
  }

  const fallbackGridIndex = Number.isInteger(datapoint?.index)
    ? datapoint.index + step
    : targetDrawerIndex;
  const columnIds = allColumns.map((i) => i?.col?.id);

  const setActiveDatapoint = (targetRow, rowData, indexFallback) => {
    if (!isNavigationCurrent()) {
      return false;
    }

    const rowDataWithId = withDrawerRowId(rowData, getDrawerRowId(targetRow));
    const targetGridIndex = syncGridRowVisibility(
      gridApi,
      targetRow,
      indexFallback,
    );

    setDatapoint({
      index: targetGridIndex,
      rowData: rowDataWithId,
      valueInfos: rowDataWithId?.valueInfos,
    });

    const nextEvalOpen = getNextEvalOpen(rowDataWithId, evalOpen, allColumns);
    if (nextEvalOpen !== undefined) {
      setEvalOpen(nextEvalOpen);
    }

    return true;
  };

  const hydrateRow = async (rowId) => {
    const newCellData = await getCellData({
      row_ids: [rowId],
      column_ids: columnIds,
    });
    const cellData = newCellData?.data?.result?.[rowId];

    return cellData ? withDrawerRowId(cellData, rowId) : null;
  };

  const reportNavigationLoadFailure = (message, error, context = {}) => {
    if (!isNavigationCurrent() || isNavigationAbortError(error)) {
      return false;
    }

    logger?.error?.(message, {
      code: error?.code,
      direction,
      message: error?.message,
      name: error?.name,
      ...context,
    });
    onNavigationLoadError?.();
    return true;
  };

  const reportMissingHydratedRow = (phase) => {
    if (!isNavigationCurrent()) {
      return false;
    }

    logger?.error?.("Failed to load next datapoint row", {
      direction,
      phase,
      reason: "missing_cell_data",
    });
    onNavigationLoadError?.();
    return true;
  };

  if (direction === "previous") {
    if (adjacentDisplayedRow?.rowData) {
      const nextRows = [...activeRows];
      nextRows[targetDrawerIndex] = adjacentDisplayedRow;
      if (
        !setActiveDatapoint(
          adjacentDisplayedRow,
          adjacentDisplayedRow.rowData,
          fallbackGridIndex,
        )
      ) {
        return false;
      }
      setRows(nextRows);
      return true;
    }

    if (adjacentDisplayedRow === null) {
      return false;
    }

    if (
      adjacentDisplayedRow === undefined &&
      !activeRows[targetDrawerIndex]?.rowData &&
      !rowsWereResynced
    ) {
      const displayedRows = getDisplayedRowsSnapshot();
      const displayedDrawerIndex = getDrawerRowPosition(
        displayedRows,
        datapoint,
      );

      if (displayedRows[displayedDrawerIndex - 1]?.rowData) {
        activeRows = displayedRows;
        currentDrawerIndex = displayedDrawerIndex;
        targetDrawerIndex = currentDrawerIndex + step;
        rowsWereResynced = true;
      }
    }

    const targetRow = activeRows[targetDrawerIndex];
    const rowData = targetRow?.rowData;

    if (!targetRow || !rowData) {
      return false;
    }

    if (!setActiveDatapoint(targetRow, rowData, fallbackGridIndex)) {
      return false;
    }

    if (rowsWereResynced) {
      setRows(activeRows);
    }
    return true;
  }

  const cachedTarget = activeRows[targetDrawerIndex];
  const cachedTargetMatchesAdjacent =
    adjacentDisplayedRow === undefined ||
    rowIdsMatch(
      getDrawerRowId(cachedTarget),
      getDrawerRowId(adjacentDisplayedRow),
    );

  if (adjacentDisplayedRow?.rowData) {
    const nextRows = [...activeRows];
    nextRows[targetDrawerIndex] = adjacentDisplayedRow;
    if (
      !setActiveDatapoint(
        adjacentDisplayedRow,
        adjacentDisplayedRow.rowData,
        fallbackGridIndex,
      )
    ) {
      return false;
    }
    setRows(nextRows);
    return true;
  }

  if (cachedTarget?.rowData && cachedTargetMatchesAdjacent) {
    if (
      !setActiveDatapoint(cachedTarget, cachedTarget.rowData, fallbackGridIndex)
    ) {
      return false;
    }
    if (rowsWereResynced) {
      setRows(activeRows);
    }
    return true;
  }

  if (cachedTarget && !cachedTarget.rowData && cachedTargetMatchesAdjacent) {
    if (!isNavigationCurrent()) {
      return false;
    }

    try {
      const rowDataWithId = await hydrateRow(cachedTarget.id);

      if (!rowDataWithId) {
        reportMissingHydratedRow("cached-row-hydration");
        return false;
      }

      if (!isNavigationCurrent()) {
        return false;
      }

      const targetRow = {
        ...cachedTarget,
        rowData: rowDataWithId,
      };
      if (!setActiveDatapoint(targetRow, rowDataWithId, fallbackGridIndex)) {
        return false;
      }
      setRows((prev) => {
        const newRows = rowsWereResynced ? [...activeRows] : [...prev];
        newRows[targetDrawerIndex] = targetRow;
        return newRows;
      });
      return true;
    } catch (error) {
      reportNavigationLoadFailure("Failed to load next datapoint row", error, {
        phase: "cached-row-hydration",
      });
      return false;
    }
  }

  const mergedRows = activeRows.slice(0, currentDrawerIndex + 1);
  const boundaryTargetDrawerIndex = mergedRows.length;

  try {
    if (!isNavigationCurrent()) {
      return false;
    }

    const nextIds = await getNextItemIds({
      ...(getNextRowsRequestParams?.() ?? {}),
      row_id: datapoint?.rowData?.rowId,
    });
    if (!isNavigationCurrent()) {
      return false;
    }
    const newIds = getAdjacentRowIds(nextIds?.data?.result, "next");

    newIds?.forEach((id, index) => {
      mergedRows.push({
        rowData: null,
        id,
        rowIndex: Number.isInteger(datapoint?.index)
          ? datapoint.index + index + 1
          : undefined,
      });
    });
  } catch (error) {
    reportNavigationLoadFailure("Failed to load next datapoint ids", error, {
      phase: "next-boundary-ids",
    });
  }

  const nextId = mergedRows[boundaryTargetDrawerIndex]?.id;
  if (!nextId) {
    if (isNavigationCurrent()) {
      setRows(mergedRows);
    }
    return false;
  }

  try {
    const rowDataWithId = await hydrateRow(nextId);

    if (!rowDataWithId) {
      reportMissingHydratedRow("next-boundary-row-hydration");
      if (isNavigationCurrent()) {
        setRows(mergedRows);
      }
      return false;
    }

    if (!isNavigationCurrent()) {
      return false;
    }

    mergedRows[boundaryTargetDrawerIndex] = {
      ...mergedRows[boundaryTargetDrawerIndex],
      rowData: rowDataWithId,
    };
    if (
      !setActiveDatapoint(
        mergedRows[boundaryTargetDrawerIndex],
        rowDataWithId,
        fallbackGridIndex,
      )
    ) {
      return false;
    }
    setRows(mergedRows);
    return true;
  } catch (error) {
    reportNavigationLoadFailure("Failed to load next datapoint row", error, {
      phase: "next-boundary-row-hydration",
    });
    if (isNavigationCurrent()) {
      setRows(mergedRows);
    }
    return false;
  }
};

export const createDrawerNavigationHandler =
  ({
    getNavigationParams,
    isNavigatingRef,
    logger,
    navigateRows = navigateDrawerRows,
    navigationInFlightRef,
  }) =>
  async (direction) => {
    const existingNavigation = navigationInFlightRef.current;
    if (existingNavigation?.isCurrent?.() ?? Boolean(existingNavigation)) {
      return;
    }

    const navigationToken = Symbol("drawer-navigation");
    let navigated = false;
    let navigationParams;

    try {
      navigationParams = getNavigationParams();
      navigationInFlightRef.current = {
        isCurrent: navigationParams.isNavigationCurrent,
        token: navigationToken,
      };
      isNavigatingRef.current = true;
      navigated = await navigateRows({
        ...navigationParams,
        direction,
      });
    } catch (error) {
      const isNavigationCurrent =
        navigationParams?.isNavigationCurrent?.() ?? true;
      if (isNavigationCurrent && !isNavigationAbortError(error)) {
        logger?.error?.("Failed to navigate datapoint drawer rows", {
          code: error?.code,
          direction,
          message: error?.message,
          name: error?.name,
          phase: "drawer-navigation-handler",
        });
        navigationParams?.onNavigationLoadError?.();
      }
    } finally {
      if (navigationInFlightRef.current?.token === navigationToken) {
        if (!navigated) {
          isNavigatingRef.current = false;
        }
        navigationInFlightRef.current = false;
      }
    }
  };
