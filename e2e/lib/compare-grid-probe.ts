import type { Page } from "@playwright/test";

// Test-only bridge to CompareData's existing refs. Nothing is added to the app,
// window, or its build configuration. React 18 attaches this fiber to host DOM
// nodes in both dev and production builds; fail loudly if that contract changes.
// The refs are read-only here: in particular this helper NEVER clears compareId.
type CompareRef = { readonly current: string | null };
type LoadParams = {
  request: { startRow: number; endRow: number };
  success: (result: { rowData: unknown[]; rowCount: number }) => void;
  fail: () => void;
};
type Datasource = { getRows: (params: LoadParams) => void | Promise<void> };
type GridApi = {
  getGridOption: (name: "serverSideDatasource") => Datasource | undefined;
  getDisplayedRowCount: () => number;
  getServerSideGroupLevelState: () => {
    route: string[];
    rowCount: number;
    lastRowIndexKnown: boolean;
  }[];
  refreshServerSide: (params?: { purge?: boolean }) => void;
};
type Fiber = {
  return?: Fiber;
  memoizedProps?: {
    gridApiRef?: { current?: { api?: GridApi } };
    compareId?: CompareRef;
  };
};

export async function observeCompareGrid(page: Page) {
  return page
    .locator(".experiment-view-grid .ag-root-wrapper")
    .evaluateHandle((element) => {
      const key = Object.keys(element).find((name) =>
        name.startsWith("__reactFiber$"),
      );
      let fiber: Fiber | undefined = key
        ? (element as unknown as Record<string, Fiber>)[key]
        : undefined;
      let api: GridApi | undefined;
      let compareId: CompareRef | undefined;
      while (fiber) {
        const props = fiber.memoizedProps;
        if (props?.gridApiRef?.current?.api && props.compareId) {
          api = props.gridApiRef.current.api;
          compareId = props.compareId;
          break;
        }
        fiber = fiber.return;
      }
      if (
        !api ||
        !compareId ||
        typeof api.refreshServerSide !== "function" ||
        typeof api.getServerSideGroupLevelState !== "function"
      ) {
        throw new Error(
          "CompareData grid refs unavailable; update the test-only React bridge",
        );
      }
      const gridApi = api;
      const idRef = compareId;
      const datasource = gridApi.getGridOption("serverSideDatasource");
      if (!datasource)
        throw new Error("Expected the real CompareData SSRM datasource");
      const originalGetRows = datasource.getRows;
      const originalRefresh = gridApi.refreshServerSide;
      const requests: {
        startRow: number;
        endRow: number;
        compareId: string | null;
      }[] = [];
      const successes: {
        rows: number;
        rowCount: number;
        compareId: string | null;
      }[] = [];
      const refreshes: { purge: boolean; compareId: string | null }[] = [];
      let failures = 0;

      // Observe calls initiated by the REAL LazyStore. Do not invoke getRows
      // ourselves, replace the datasource, supply data, or simulate completion.
      const observedGetRows: Datasource["getRows"] = function (params) {
        requests.push({
          startRow: params.request.startRow,
          endRow: params.request.endRow,
          compareId: idRef.current,
        });
        return originalGetRows.call(datasource, {
          ...params,
          success(result) {
            successes.push({
              rows: result.rowData.length,
              rowCount: result.rowCount,
              compareId: idRef.current,
            });
            params.success(result);
          },
          fail() {
            failures += 1;
            params.fail();
          },
        });
      };
      const observedRefresh: GridApi["refreshServerSide"] = function (params) {
        refreshes.push({
          purge: params?.purge === true,
          compareId: idRef.current,
        });
        return originalRefresh.call(gridApi, params);
      };
      datasource.getRows = observedGetRows;
      gridApi.refreshServerSide = observedRefresh;

      const snapshot = () => ({
        compareId: idRef.current,
        displayedRows: gridApi.getDisplayedRowCount(),
        stores: gridApi
          .getServerSideGroupLevelState()
          .map(({ route, rowCount, lastRowIndexKnown }) => ({
            route,
            rowCount,
            lastRowIndexKnown,
          })),
        datasourceUnchanged:
          gridApi.getGridOption("serverSideDatasource") === datasource,
        requests: [...requests],
        successes: [...successes],
        refreshes: [...refreshes],
        failures,
      });
      return {
        snapshot,
        refresh(purge: boolean) {
          gridApi.refreshServerSide({ purge });
          // AG Grid 35 refreshStore(true) creates a one-row unknown-size cache;
          // markNodesForRefresh() does the same for a known-empty non-purge cache.
          // Capture that synchronous store transition before its async load check.
          return snapshot();
        },
        restore() {
          if (datasource.getRows === observedGetRows)
            datasource.getRows = originalGetRows;
          if (gridApi.refreshServerSide === observedRefresh)
            gridApi.refreshServerSide = originalRefresh;
        },
      };
    });
}
