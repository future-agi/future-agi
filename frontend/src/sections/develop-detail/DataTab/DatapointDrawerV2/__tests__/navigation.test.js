import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  canNavigatePreviousDrawerRow,
  canNavigatePreviousDrawerRowFromGrid,
  createDrawerNavigationHandler,
  getActiveRowClass,
  getDisplayedDrawerRows,
  getDrawerRowPosition,
  getGridRowNode,
  handleDrawerNavigationShortcut,
  isDrawerNavigationSourceCurrent,
  navigateDrawerRows,
  redrawActiveRowHighlight,
  resolveGridRowIndex,
  shouldIgnoreDrawerNavigationShortcut,
  syncGridRowVisibility,
  useActiveRowHighlightRedraw,
  withDrawerRowId,
} from "../navigation";

describe("DatapointDrawerV2 navigation highlight Unit", () => {
  it("finds the drawer row by stable row id instead of treating grid row index as array index", () => {
    const rows = [
      { id: "row-a", rowData: { rowId: "row-a" }, rowIndex: 40 },
      { id: "row-b", rowData: { rowId: "row-b" }, rowIndex: 41 },
    ];

    expect(
      getDrawerRowPosition(rows, {
        index: 41,
        rowData: { rowId: "row-b" },
      }),
    ).toBe(1);
  });

  it("keeps a legacy index fallback only when it still points at the same row object", () => {
    const rowData = { name: "legacy-row-without-id" };
    const rows = [{ rowData: {} }, { rowData }];

    expect(getDrawerRowPosition(rows, { index: 1, rowData })).toBe(1);
  });

  it("matches numeric and string row ids when locating grid nodes", () => {
    const node = { id: "123", rowIndex: 6 };
    const gridApi = {
      getRowNode: vi.fn((id) => (id === "123" ? node : undefined)),
    };

    expect(getGridRowNode(gridApi, 123)).toBe(node);
    expect(resolveGridRowIndex(gridApi, { id: 123 }, 2)).toBe(6);
    expect(gridApi.getRowNode).toHaveBeenCalledWith("123");
  });

  it("checks stale drawer navigation against the current store datapoint identity", () => {
    const sourceRowData = { rowId: "row-a" };
    const navigationRequestIdRef = { current: 7 };

    expect(
      isDrawerNavigationSourceCurrent({
        getCurrentDatapoint: () => ({ rowData: { rowId: "row-a" } }),
        navigationRequestIdRef,
        requestId: 7,
        sourceRowData,
        sourceRowId: "row-a",
      }),
    ).toBe(true);
    expect(
      isDrawerNavigationSourceCurrent({
        getCurrentDatapoint: () => ({ rowData: { rowId: "row-c" } }),
        navigationRequestIdRef,
        requestId: 7,
        sourceRowData,
        sourceRowId: "row-a",
      }),
    ).toBe(false);
    expect(
      isDrawerNavigationSourceCurrent({
        getCurrentDatapoint: () => ({ rowData: sourceRowData }),
        navigationRequestIdRef,
        requestId: 6,
        sourceRowData,
        sourceRowId: "row-a",
      }),
    ).toBe(false);

    const noRowIdData = { value: "legacy" };
    expect(
      isDrawerNavigationSourceCurrent({
        getCurrentDatapoint: () => ({ rowData: noRowIdData }),
        navigationRequestIdRef,
        requestId: 7,
        sourceRowData: noRowIdData,
      }),
    ).toBe(true);
    expect(
      isDrawerNavigationSourceCurrent({
        getCurrentDatapoint: () => ({ rowData: { value: "legacy" } }),
        navigationRequestIdRef,
        requestId: 7,
        sourceRowData: noRowIdData,
      }),
    ).toBe(false);
  });

  it("collects displayed drawer rows with their current grid row indexes", () => {
    const gridApi = {
      forEachNode: vi.fn((visitor) => {
        visitor({
          displayed: true,
          id: "row-b",
          rowIndex: 42,
          data: { rowId: "row-b" },
        });
        visitor({
          displayed: false,
          id: "row-hidden",
          rowIndex: 41,
          data: { rowId: "row-hidden" },
        });
        visitor({
          displayed: true,
          id: "row-a",
          rowIndex: 40,
          data: { rowId: "row-a" },
        });
      }),
    };

    expect(getDisplayedDrawerRows(gridApi)).toEqual([
      { id: "row-a", rowData: { rowId: "row-a" }, rowIndex: 40 },
      { id: "row-b", rowData: { rowId: "row-b" }, rowIndex: 42 },
    ]);
  });

  it("scrolls the target grid node into view and returns the node row index", () => {
    const node = { id: "row-b", rowIndex: 44 };
    const gridApi = {
      ensureIndexVisible: vi.fn(),
      ensureNodeVisible: vi.fn(),
      getRowNode: vi.fn(() => node),
    };

    expect(syncGridRowVisibility(gridApi, { id: "row-b" }, 41)).toBe(44);
    expect(gridApi.ensureNodeVisible).toHaveBeenCalledWith(node);
    expect(gridApi.ensureIndexVisible).not.toHaveBeenCalled();
  });

  it("falls back to the calculated grid row index when AG Grid has not loaded the target node", () => {
    const gridApi = {
      ensureIndexVisible: vi.fn(),
      ensureNodeVisible: vi.fn(),
      getRowNode: vi.fn(() => undefined),
    };

    expect(syncGridRowVisibility(gridApi, { id: "row-z" }, 73)).toBe(73);
    expect(gridApi.ensureIndexVisible).toHaveBeenCalledWith(73);
    expect(gridApi.ensureNodeVisible).not.toHaveBeenCalled();
  });

  it("redraws only the affected active-row nodes when identities resolve", () => {
    const rowANode = { id: "row-a" };
    const rowBNode = { id: "row-b" };
    const gridApi = {
      getRowNode: vi.fn((id) =>
        id === "row-a" ? rowANode : id === "row-b" ? rowBNode : undefined,
      ),
      redrawRows: vi.fn(),
    };
    const gridApiRef = { current: { api: gridApi } };

    const { rerender } = renderHook(
      ({ activeRowId }) => useActiveRowHighlightRedraw(gridApiRef, activeRowId),
      { initialProps: { activeRowId: "row-a" } },
    );

    expect(gridApi.redrawRows).toHaveBeenNthCalledWith(1, {
      rowNodes: [rowANode],
    });
    rerender({ activeRowId: "row-b" });
    expect(gridApi.redrawRows).toHaveBeenNthCalledWith(2, {
      rowNodes: [rowANode, rowBNode],
    });

    redrawActiveRowHighlight(gridApi, "row-b", "row-a");
    expect(gridApi.redrawRows).toHaveBeenNthCalledWith(3, {
      rowNodes: [rowBNode, rowANode],
    });
  });

  it("retries active-row redraw when the grid api becomes available after mount", () => {
    const rowNode = { id: "row-a", rowIndex: 4 };
    const gridApiRef = { current: null };

    const { rerender } = renderHook(
      ({ redrawKey }) =>
        useActiveRowHighlightRedraw(gridApiRef, "row-a", redrawKey),
      { initialProps: { redrawKey: 0 } },
    );

    const gridApi = {
      getRowNode: vi.fn((id) => (id === "row-a" ? rowNode : undefined)),
      redrawRows: vi.fn(),
    };
    gridApiRef.current = { api: gridApi };
    rerender({ redrawKey: 1 });

    expect(gridApi.redrawRows).toHaveBeenCalledWith({ rowNodes: [rowNode] });
  });

  it("uses displayed row indexes before falling back to redrawing rendered rows", () => {
    const rowSeven = { id: "displayed-7", rowIndex: 7 };
    const gridApi = {
      getDisplayedRowAtIndex: vi.fn((index) =>
        index === 7 ? rowSeven : undefined,
      ),
      getRowNode: vi.fn(() => undefined),
      redrawRows: vi.fn(),
    };

    redrawActiveRowHighlight(gridApi, undefined, 7);

    expect(gridApi.redrawRows).toHaveBeenCalledWith({
      rowNodes: [rowSeven],
    });
    expect(gridApi.redrawRows).not.toHaveBeenCalledWith();
  });

  it("falls back to redrawing rendered rows when active-row nodes are unavailable", () => {
    const gridApi = {
      getRowNode: vi.fn(() => undefined),
      redrawRows: vi.fn(),
    };

    redrawActiveRowHighlight(gridApi, "missing-a", "missing-b");

    expect(gridApi.redrawRows).toHaveBeenCalledWith();
  });

  it("returns active-row by stable row id before falling back to row index", () => {
    expect(
      getActiveRowClass(
        { data: { rowId: "row-a" }, node: { rowIndex: 7 } },
        { index: 99, rowData: { rowId: "row-a" } },
      ),
    ).toBe("active-row");
    expect(
      getActiveRowClass(
        { data: { rowId: "row-b" }, node: { rowIndex: 7 } },
        { index: 7, rowData: { rowId: "row-a" } },
      ),
    ).toBe("");
    expect(getActiveRowClass({ node: { rowIndex: 7 } }, 7)).toBe("active-row");
  });

  it("preserves row ids on hydrated cell payloads", () => {
    expect(withDrawerRowId({ value: "loaded" }, "row-b")).toEqual({
      value: "loaded",
      rowId: "row-b",
    });
    expect(withDrawerRowId({ rowId: "existing" }, "row-b")).toEqual({
      rowId: "existing",
    });
  });

  it("reports previous navigation availability from the cache or current grid rows", () => {
    const rows = [{ id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } }];
    const datapoint = { index: 41, rowData: { rowId: "row-b" } };

    expect(canNavigatePreviousDrawerRow(rows, datapoint)).toBe(false);
    expect(
      canNavigatePreviousDrawerRow(
        [{ id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } }, ...rows],
        datapoint,
      ),
    ).toBe(true);
    expect(canNavigatePreviousDrawerRowFromGrid(rows, datapoint)).toBe(false);

    const gridApi = {
      forEachNode: vi.fn((visitor) => {
        [
          { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
          { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
        ].forEach((row) =>
          visitor({
            data: row.rowData,
            displayed: true,
            id: row.id,
            rowIndex: row.rowIndex,
          }),
        );
      }),
    };

    expect(canNavigatePreviousDrawerRowFromGrid(rows, datapoint, gridApi)).toBe(
      true,
    );
    expect(
      canNavigatePreviousDrawerRowFromGrid(rows, datapoint, gridApi, {
        allowFullScan: false,
      }),
    ).toBe(false);
  });

  it("ignores drawer navigation shortcuts only from text-entry targets", () => {
    const button = document.createElement("button");
    const buttonChild = document.createElement("span");
    button.appendChild(buttonChild);
    const input = document.createElement("input");
    const textbox = document.createElement("div");
    textbox.setAttribute("role", "textbox");
    const editableTarget = document.createElement("div");
    editableTarget.setAttribute("contenteditable", "true");
    const plainTarget = document.createElement("div");

    expect(shouldIgnoreDrawerNavigationShortcut(button)).toBe(false);
    expect(shouldIgnoreDrawerNavigationShortcut(buttonChild)).toBe(false);
    expect(shouldIgnoreDrawerNavigationShortcut(input)).toBe(true);
    expect(shouldIgnoreDrawerNavigationShortcut(textbox)).toBe(true);
    expect(shouldIgnoreDrawerNavigationShortcut(editableTarget)).toBe(true);
    expect(shouldIgnoreDrawerNavigationShortcut(plainTarget)).toBe(false);
    expect(shouldIgnoreDrawerNavigationShortcut(null)).toBe(false);
  });

  const createNavigationHarness = (overrides = {}) => {
    const rows = overrides.rows ?? [
      {
        id: "row-a",
        rowIndex: 40,
        rowData: { rowId: "row-a", metricField: { cellValue: "a" } },
      },
      {
        id: "row-b",
        rowIndex: 41,
        rowData: { rowId: "row-b", metricField: { cellValue: "b" } },
      },
    ];
    const gridApi = overrides.gridApi ?? {
      ensureIndexVisible: vi.fn(),
      ensureNodeVisible: vi.fn(),
      getRowNode: vi.fn((id) => {
        const row = rows.find((candidate) => candidate.id === id);
        return row ? { id, rowIndex: row.rowIndex } : undefined;
      }),
    };

    return {
      allColumns: overrides.allColumns ?? [
        { field: "metricField", col: { id: "column-1", sourceId: "metric-1" } },
      ],
      datapoint: overrides.datapoint ?? {
        index: 40,
        rowData: { rowId: "row-a" },
      },
      direction: overrides.direction ?? "next",
      evalOpen: overrides.evalOpen,
      getCellData: overrides.getCellData ?? vi.fn(),
      getNextItemIds: overrides.getNextItemIds ?? vi.fn(),
      getNextRowsRequestParams:
        overrides.getNextRowsRequestParams ?? vi.fn(() => ({})),
      gridApi,
      isNavigationCurrent: overrides.isNavigationCurrent,
      logger: { error: vi.fn() },
      onNavigationLoadError: overrides.onNavigationLoadError,
      rows,
      setDatapoint: vi.fn(),
      setEvalOpen: vi.fn(),
      setRows: vi.fn(),
    };
  };

  it("navigates cached next rows using the target grid row index instead of the drawer array index", async () => {
    const harness = createNavigationHarness({
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: harness.rows[1].rowData,
      valueInfos: undefined,
    });
    expect(harness.gridApi.ensureNodeVisible).toHaveBeenCalledWith({
      id: "row-b",
      rowIndex: 41,
    });
  });

  it("composes drawer navigation with active-row classing and redraws the previous/current grid rows", async () => {
    const rowANode = { id: "row-a", rowIndex: 40 };
    const rowBNode = { id: "row-b", rowIndex: 41 };
    const rows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
    ];
    const gridApi = {
      ensureNodeVisible: vi.fn(),
      getRowNode: vi.fn((id) =>
        id === "row-a" ? rowANode : id === "row-b" ? rowBNode : undefined,
      ),
      redrawRows: vi.fn(),
      setFocusedCell: vi.fn(),
    };
    const harness = createNavigationHarness({
      rows,
      gridApi,
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    const activeDatapoint = harness.setDatapoint.mock.calls[0][0];
    redrawActiveRowHighlight(
      gridApi,
      harness.datapoint.rowData.rowId,
      activeDatapoint.rowData.rowId,
    );

    expect(
      getActiveRowClass(
        { data: rows[0].rowData, node: rowANode },
        activeDatapoint,
      ),
    ).toBe("");
    expect(
      getActiveRowClass(
        { data: rows[1].rowData, node: rowBNode },
        activeDatapoint,
      ),
    ).toBe("active-row");
    expect(gridApi.redrawRows).toHaveBeenCalledWith({
      rowNodes: [rowANode, rowBNode],
    });
    expect(gridApi.ensureNodeVisible).toHaveBeenCalledWith(rowBNode);
    expect(gridApi.setFocusedCell).not.toHaveBeenCalled();
  });

  it("guards drawer navigation while a navigation attempt is in flight", async () => {
    let resolveNavigation;
    const navigationPromise = new Promise((resolve) => {
      resolveNavigation = resolve;
    });
    const navigateRows = vi.fn(() => navigationPromise);
    const navigationInFlightRef = { current: false };
    const isNavigatingRef = { current: false };
    const onNavigate = createDrawerNavigationHandler({
      getNavigationParams: () => ({ rows: [] }),
      isNavigatingRef,
      logger: { error: vi.fn() },
      navigateRows,
      navigationInFlightRef,
    });

    const firstNavigation = onNavigate("next");
    const secondNavigation = onNavigate("next");

    expect(navigateRows).toHaveBeenCalledTimes(1);
    expect(isNavigatingRef.current).toBe(true);
    expect(navigationInFlightRef.current).toBeTruthy();
    await expect(secondNavigation).resolves.toBeUndefined();

    resolveNavigation(false);
    await firstNavigation;

    expect(isNavigatingRef.current).toBe(false);
    expect(navigationInFlightRef.current).toBe(false);

    navigateRows.mockResolvedValueOnce(true);
    await onNavigate("next");

    expect(navigateRows).toHaveBeenCalledTimes(2);
    expect(isNavigatingRef.current).toBe(true);
    expect(navigationInFlightRef.current).toBe(false);
  });

  it("allows a new drawer source to navigate while a stale source is still settling", async () => {
    const navigationInFlightRef = {
      current: { isCurrent: () => false, token: Symbol("old-navigation") },
    };
    const isNavigatingRef = { current: false };
    const navigateRows = vi.fn().mockResolvedValue(true);
    const onNavigate = createDrawerNavigationHandler({
      getNavigationParams: () => ({
        isNavigationCurrent: () => true,
        rows: [],
      }),
      isNavigatingRef,
      logger: { error: vi.fn() },
      navigateRows,
      navigationInFlightRef,
    });

    await onNavigate("next");

    expect(navigateRows).toHaveBeenCalledTimes(1);
    expect(isNavigatingRef.current).toBe(true);
    expect(navigationInFlightRef.current).toBe(false);
  });

  it("does not let stale navigation completions clear a newer in-flight token", async () => {
    let resolveFirstNavigation;
    const firstNavigationPromise = new Promise((resolve) => {
      resolveFirstNavigation = resolve;
    });
    const newerNavigationToken = Symbol("newer-navigation");
    const navigateRows = vi.fn(() => firstNavigationPromise);
    const navigationInFlightRef = { current: false };
    const isNavigatingRef = { current: false };
    const onNavigate = createDrawerNavigationHandler({
      getNavigationParams: () => ({ rows: [] }),
      isNavigatingRef,
      logger: { error: vi.fn() },
      navigateRows,
      navigationInFlightRef,
    });

    const firstNavigation = onNavigate("next");
    navigationInFlightRef.current = newerNavigationToken;
    resolveFirstNavigation(false);
    await firstNavigation;

    expect(navigationInFlightRef.current).toBe(newerNavigationToken);
  });

  it("does not show load errors for stale handler-level navigation failures", async () => {
    let rejectNavigation;
    let isCurrent = true;
    const navigationPromise = new Promise((_, reject) => {
      rejectNavigation = reject;
    });
    const onNavigationLoadError = vi.fn();
    const navigateRows = vi.fn(() => navigationPromise);
    const navigationInFlightRef = { current: false };
    const isNavigatingRef = { current: false };
    const logger = { error: vi.fn() };
    const onNavigate = createDrawerNavigationHandler({
      getNavigationParams: () => ({
        isNavigationCurrent: () => isCurrent,
        onNavigationLoadError,
        rows: [],
      }),
      isNavigatingRef,
      logger,
      navigateRows,
      navigationInFlightRef,
    });

    const navigation = onNavigate("next");
    isCurrent = false;
    rejectNavigation(new Error("late failure"));
    await navigation;

    expect(onNavigationLoadError).not.toHaveBeenCalled();
    expect(logger.error).not.toHaveBeenCalled();
    expect(navigationInFlightRef.current).toBe(false);
  });

  it("handles drawer keyboard navigation only when the target and loading state allow it", () => {
    const createEvent = (overrides = {}) => ({
      altKey: false,
      ctrlKey: false,
      key: "j",
      metaKey: false,
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
      target: document.createElement("div"),
      ...overrides,
    });
    const createState = (overrides = {}) => ({
      canNavigatePrevious: true,
      datapointIndex: 1,
      enabled: true,
      navLoading: false,
      onNavigate: vi.fn(),
      totalRowCount: 3,
      ...overrides,
    });

    const nextEvent = createEvent();
    const nextState = createState();
    expect(handleDrawerNavigationShortcut(nextEvent, nextState)).toBe(true);
    expect(nextEvent.preventDefault).toHaveBeenCalled();
    expect(nextEvent.stopPropagation).toHaveBeenCalled();
    expect(nextState.onNavigate).toHaveBeenCalledWith("next");

    const loadingEvent = createEvent();
    const loadingState = createState({ navLoading: true });
    expect(handleDrawerNavigationShortcut(loadingEvent, loadingState)).toBe(
      false,
    );
    expect(loadingState.onNavigate).not.toHaveBeenCalled();

    const previousDisabledEvent = createEvent({ key: "k" });
    const previousDisabledState = createState({ canNavigatePrevious: false });
    expect(
      handleDrawerNavigationShortcut(
        previousDisabledEvent,
        previousDisabledState,
      ),
    ).toBe(false);
    expect(previousDisabledState.onNavigate).not.toHaveBeenCalled();

    const previousEvent = createEvent({ key: "K" });
    const previousState = createState();
    expect(handleDrawerNavigationShortcut(previousEvent, previousState)).toBe(
      true,
    );
    expect(previousState.onNavigate).toHaveBeenCalledWith("previous");

    const buttonEvent = createEvent({
      target: document.createElement("button"),
    });
    const buttonState = createState();
    expect(handleDrawerNavigationShortcut(buttonEvent, buttonState)).toBe(true);
    expect(buttonState.onNavigate).toHaveBeenCalledWith("next");

    const interactiveEvent = createEvent({
      target: document.createElement("input"),
    });
    const interactiveState = createState();
    expect(
      handleDrawerNavigationShortcut(interactiveEvent, interactiveState),
    ).toBe(false);
    expect(interactiveState.onNavigate).not.toHaveBeenCalled();

    const lastRowEvent = createEvent();
    const lastRowState = createState({ datapointIndex: 2 });
    expect(handleDrawerNavigationShortcut(lastRowEvent, lastRowState)).toBe(
      false,
    );
    expect(lastRowState.onNavigate).not.toHaveBeenCalled();
  });

  it("preserves the existing eval detail merge behavior during navigation", async () => {
    const evalOpen = {
      evalMetricId: "metric-1",
      metadata: { runPrompt: true },
      cellValue: "old score",
    };
    const harness = createNavigationHarness({ evalOpen });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setEvalOpen).toHaveBeenCalledWith({
      ...evalOpen,
      cellValue: "b",
    });

    const missingEvalHarness = createNavigationHarness({
      evalOpen,
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
      ],
    });

    await expect(navigateDrawerRows(missingEvalHarness)).resolves.toBe(true);

    expect(missingEvalHarness.setEvalOpen).toHaveBeenCalledWith(evalOpen);
  });

  it("uses AG Grid row nodes when drawer row indexes are stale", async () => {
    const rows = [
      { id: "row-a", rowIndex: 0, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 1, rowData: { rowId: "row-b" } },
    ];
    const harness = createNavigationHarness({
      rows,
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        getRowNode: vi.fn((id) => {
          const nodes = {
            "row-a": { id: "row-a", rowIndex: 72 },
            "row-b": { id: "row-b", rowIndex: 73 },
          };
          return nodes[id];
        }),
      },
      datapoint: { index: 72, rowData: { rowId: "row-a" } },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 73,
      rowData: rows[1].rowData,
      valueInfos: undefined,
    });
  });

  it("resyncs displayed rows when the cached drawer snapshot no longer contains the active row", async () => {
    const displayedRows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
    ];
    const harness = createNavigationHarness({
      rows: [
        { id: "stale-row", rowIndex: 12, rowData: { rowId: "stale-row" } },
      ],
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        forEachNode: vi.fn((visitor) => {
          displayedRows.forEach((row) =>
            visitor({
              data: row.rowData,
              displayed: true,
              id: row.id,
              rowIndex: row.rowIndex,
            }),
          );
        }),
        getRowNode: vi.fn((id) => {
          const row = displayedRows.find((candidate) => candidate.id === id);
          return row ? { id, rowIndex: row.rowIndex } : undefined;
        }),
      },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setRows).toHaveBeenCalledWith(displayedRows);
    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: displayedRows[1].rowData,
      valueInfos: undefined,
    });
  });

  it("prefers live displayed row order when the cached active row has a stale neighbor", async () => {
    const cachedRows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-old", rowIndex: 41, rowData: { rowId: "row-old" } },
    ];
    const displayedRows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-new", rowIndex: 41, rowData: { rowId: "row-new" } },
    ];
    const harness = createNavigationHarness({
      rows: cachedRows,
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        forEachNode: vi.fn((visitor) => {
          displayedRows.forEach((row) =>
            visitor({
              data: row.rowData,
              displayed: true,
              id: row.id,
              rowIndex: row.rowIndex,
            }),
          );
        }),
        getRowNode: vi.fn((id) => {
          const row = displayedRows.find((candidate) => candidate.id === id);
          return row ? { id, rowIndex: row.rowIndex } : undefined;
        }),
      },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setRows).toHaveBeenCalledWith(displayedRows);
    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: displayedRows[1].rowData,
      valueInfos: undefined,
    });
  });

  it("prefers fresh live displayed row data over cached data with the same row id", async () => {
    const rows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b", value: "old" } },
    ];
    const liveRow = {
      id: "row-b",
      rowIndex: 41,
      rowData: { rowId: "row-b", value: "fresh" },
    };
    const harness = createNavigationHarness({
      rows,
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        getDisplayedRowAtIndex: vi.fn((index) =>
          index === 41
            ? { id: "row-b", rowIndex: 41, data: liveRow.rowData }
            : null,
        ),
        getRowNode: vi.fn((id) => {
          const nodes = {
            "row-a": { id: "row-a", rowIndex: 40 },
            "row-b": { id: "row-b", rowIndex: 41 },
          };
          return nodes[id];
        }),
      },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: liveRow.rowData,
      valueInfos: undefined,
    });
    expect(harness.setRows).toHaveBeenCalledWith([rows[0], liveRow]);
  });

  it("does not trust a stale cached next row when the live grid cannot confirm it", async () => {
    const rows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-old", rowIndex: 41, rowData: { rowId: "row-old" } },
    ];
    const harness = createNavigationHarness({
      rows,
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        getDisplayedRowAtIndex: vi.fn((index) =>
          index === 40
            ? { id: "row-a", rowIndex: 40, data: { rowId: "row-a" } }
            : null,
        ),
        getRowNode: vi.fn((id) =>
          id === "row-a" ? { id: "row-a", rowIndex: 40 } : undefined,
        ),
      },
      getNextItemIds: vi.fn().mockResolvedValue({
        data: { result: { next: { rowId: ["row-new"] } } },
      }),
      getCellData: vi.fn().mockResolvedValue({
        data: { result: { "row-new": { value: "fresh" } } },
      }),
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.getNextItemIds).toHaveBeenCalledWith({ row_id: "row-a" });
    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: { value: "fresh", rowId: "row-new" },
      valueInfos: undefined,
    });
  });

  it("does not trust a stale cached previous row when the live grid cannot confirm it", async () => {
    const rows = [
      { id: "row-old", rowIndex: 40, rowData: { rowId: "row-old" } },
      { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
    ];
    const harness = createNavigationHarness({
      rows,
      direction: "previous",
      datapoint: { index: 41, rowData: { rowId: "row-b" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        getDisplayedRowAtIndex: vi.fn((index) =>
          index === 41
            ? { id: "row-b", rowIndex: 41, data: { rowId: "row-b" } }
            : null,
        ),
        getRowNode: vi.fn((id) =>
          id === "row-b" ? { id: "row-b", rowIndex: 41 } : undefined,
        ),
      },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("does not sync stale live rows after the active drawer row changes", async () => {
    const rows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-old", rowIndex: 41, rowData: { rowId: "row-old" } },
    ];
    const harness = createNavigationHarness({
      rows,
      datapoint: { index: 40, rowData: { rowId: "row-a" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        getDisplayedRowAtIndex: vi.fn((index) => {
          const displayedRows = {
            40: { id: "row-a", rowIndex: 40, data: { rowId: "row-a" } },
            41: { id: "row-new", rowIndex: 41, data: { rowId: "row-new" } },
          };
          return displayedRows[index] ?? null;
        }),
        getRowNode: vi.fn((id) => {
          const nodes = {
            "row-a": { id: "row-a", rowIndex: 40 },
            "row-new": { id: "row-new", rowIndex: 41 },
          };
          return nodes[id];
        }),
      },
      isNavigationCurrent: () => false,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("ignores async navigation results after the active drawer row changes", async () => {
    let resolveCellData;
    let isCurrent = true;
    const cellDataPromise = new Promise((resolve) => {
      resolveCellData = resolve;
    });
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn(() => cellDataPromise),
      isNavigationCurrent: () => isCurrent,
    });

    const navigation = navigateDrawerRows(harness);
    isCurrent = false;
    resolveCellData({
      data: { result: { "row-b": { value: "late" } } },
    });

    await expect(navigation).resolves.toBe(false);
    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setEvalOpen).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("does not start async cached-row hydration when navigation is already stale", async () => {
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn().mockResolvedValue({
        data: { result: { "row-b": { value: "late" } } },
      }),
      isNavigationCurrent: () => false,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(harness.getCellData).not.toHaveBeenCalled();
    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("surfaces current navigation load failures without updating drawer state", async () => {
    const onNavigationLoadError = vi.fn();
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn().mockRejectedValue(new Error("network down")),
      onNavigationLoadError,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(onNavigationLoadError).toHaveBeenCalledTimes(1);
    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("does not surface stale navigation load failures", async () => {
    const onNavigationLoadError = vi.fn();
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn().mockRejectedValue(new Error("network down")),
      isNavigationCurrent: () => false,
      onNavigationLoadError,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(onNavigationLoadError).not.toHaveBeenCalled();
    expect(harness.logger.error).not.toHaveBeenCalled();
    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("surfaces missing cached row payloads instead of silently no-oping", async () => {
    const onNavigationLoadError = vi.fn();
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn().mockResolvedValue({
        data: { result: {} },
      }),
      onNavigationLoadError,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(onNavigationLoadError).toHaveBeenCalledTimes(1);
    expect(harness.logger.error).toHaveBeenCalledWith(
      "Failed to load next datapoint row",
      expect.objectContaining({
        direction: "next",
        phase: "cached-row-hydration",
        reason: "missing_cell_data",
      }),
    );
    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("does not log stale missing cached row payloads", async () => {
    const onNavigationLoadError = vi.fn();
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn().mockResolvedValue({
        data: { result: {} },
      }),
      isNavigationCurrent: () => false,
      onNavigationLoadError,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(onNavigationLoadError).not.toHaveBeenCalled();
    expect(harness.logger.error).not.toHaveBeenCalled();
    expect(harness.setDatapoint).not.toHaveBeenCalled();
    expect(harness.setRows).not.toHaveBeenCalled();
  });

  it("hydrates cached placeholder rows and keeps their row id for highlight matching", async () => {
    const harness = createNavigationHarness({
      rows: [
        { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
        { id: "row-b", rowIndex: 41, rowData: null },
      ],
      getCellData: vi.fn().mockResolvedValue({
        data: { result: { "row-b": { value: "loaded" } } },
      }),
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.getCellData).toHaveBeenCalledWith({
      row_ids: ["row-b"],
      column_ids: ["column-1"],
    });
    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: { value: "loaded", rowId: "row-b" },
      valueInfos: undefined,
    });
  });

  it("fetches next boundary ids with the existing endpoint payload and stores grid-index placeholders", async () => {
    const harness = createNavigationHarness({
      rows: [{ id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } }],
      getNextItemIds: vi.fn().mockResolvedValue({
        data: { result: { next: { rowId: ["row-b", "row-c"] } } },
      }),
      getCellData: vi.fn().mockResolvedValue({
        data: { result: { "row-b": { value: "loaded" } } },
      }),
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.getNextItemIds).toHaveBeenCalledWith({ row_id: "row-a" });
    expect(harness.setRows).toHaveBeenCalledWith([
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      {
        id: "row-b",
        rowIndex: 41,
        rowData: { value: "loaded", rowId: "row-b" },
      },
      { id: "row-c", rowIndex: 42, rowData: null },
    ]);
    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 41,
      rowData: { value: "loaded", rowId: "row-b" },
      valueInfos: undefined,
    });
  });

  it("forwards the current grid view context when fetching boundary row ids", async () => {
    const harness = createNavigationHarness({
      rows: [{ id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } }],
      getNextRowsRequestParams: vi.fn(() => ({
        filters: [{ columnId: "column-1", operator: "contains", value: "x" }],
        search: { key: "needle", type: ["text"] },
        sort: [{ columnId: "column-1", type: "descending" }],
      })),
      getNextItemIds: vi.fn().mockResolvedValue({
        data: { result: { next: { rowId: ["row-b"] } } },
      }),
      getCellData: vi.fn().mockResolvedValue({
        data: { result: { "row-b": { value: "loaded" } } },
      }),
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.getNextItemIds).toHaveBeenCalledWith({
      filters: [{ columnId: "column-1", operator: "contains", value: "x" }],
      row_id: "row-a",
      search: { key: "needle", type: ["text"] },
      sort: [{ columnId: "column-1", type: "descending" }],
    });
  });

  it("surfaces missing next-boundary row payloads after storing fetched ids", async () => {
    const onNavigationLoadError = vi.fn();
    const harness = createNavigationHarness({
      rows: [{ id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } }],
      getNextItemIds: vi.fn().mockResolvedValue({
        data: { result: { next: { rowId: ["row-b"] } } },
      }),
      getCellData: vi.fn().mockResolvedValue({
        data: { result: {} },
      }),
      onNavigationLoadError,
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(false);

    expect(onNavigationLoadError).toHaveBeenCalledTimes(1);
    expect(harness.logger.error).toHaveBeenCalledWith(
      "Failed to load next datapoint row",
      expect.objectContaining({
        direction: "next",
        phase: "next-boundary-row-hydration",
        reason: "missing_cell_data",
      }),
    );
    expect(harness.setRows).toHaveBeenCalledWith([
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 41, rowData: null },
    ]);
    expect(harness.setDatapoint).not.toHaveBeenCalled();
  });

  it("normalizes scalar next-boundary row ids before hydrating", async () => {
    const harness = createNavigationHarness({
      rows: [{ id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } }],
      getNextItemIds: vi.fn().mockResolvedValue({
        data: { result: { next: { rowId: "row-b" } } },
      }),
      getCellData: vi.fn().mockResolvedValue({
        data: { result: { "row-b": { value: "loaded" } } },
      }),
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setRows).toHaveBeenCalledWith([
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      {
        id: "row-b",
        rowIndex: 41,
        rowData: { value: "loaded", rowId: "row-b" },
      },
    ]);
  });

  it("navigates previous rows from the local drawer cache using the target grid row index", async () => {
    const rows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
    ];
    const harness = createNavigationHarness({
      rows,
      direction: "previous",
      datapoint: { index: 41, rowData: { rowId: "row-b" } },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 40,
      rowData: rows[0].rowData,
      valueInfos: undefined,
    });
  });

  it("navigates previous from cached rows when the current hydrated row is not in AG Grid yet", async () => {
    const rows = [
      { id: "row-a", rowIndex: 40, rowData: { rowId: "row-a" } },
      { id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } },
    ];
    const harness = createNavigationHarness({
      rows,
      direction: "previous",
      datapoint: { index: 41, rowData: { rowId: "row-b" } },
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        getDisplayedRowAtIndex: vi.fn(() => null),
        getRowNode: vi.fn((id) =>
          id === "row-a" ? { id: "row-a", rowIndex: 40 } : undefined,
        ),
      },
    });

    await expect(navigateDrawerRows(harness)).resolves.toBe(true);

    expect(harness.setDatapoint).toHaveBeenCalledWith({
      index: 40,
      rowData: rows[0].rowData,
      valueInfos: undefined,
    });
  });

  it("keeps previous navigation cache-bounded at unloaded row boundaries", async () => {
    const rows = [{ id: "row-b", rowIndex: 41, rowData: { rowId: "row-b" } }];
    const datapoint = { index: 41, rowData: { rowId: "row-b" } };
    const harness = createNavigationHarness({
      rows,
      direction: "previous",
      datapoint,
      gridApi: {
        ensureIndexVisible: vi.fn(),
        ensureNodeVisible: vi.fn(),
        forEachNode: vi.fn((visitor) => {
          visitor({
            data: rows[0].rowData,
            displayed: true,
            id: rows[0].id,
            rowIndex: rows[0].rowIndex,
          });
        }),
        getRowNode: vi.fn((id) =>
          id === "row-b" ? { id: "row-b", rowIndex: 41 } : undefined,
        ),
      },
    });

    expect(
      canNavigatePreviousDrawerRowFromGrid(rows, datapoint, harness.gridApi),
    ).toBe(false);
    await expect(navigateDrawerRows(harness)).resolves.toBe(false);
    expect(harness.getNextItemIds).not.toHaveBeenCalled();
    expect(harness.getCellData).not.toHaveBeenCalled();
    expect(harness.setDatapoint).not.toHaveBeenCalled();
  });
});
