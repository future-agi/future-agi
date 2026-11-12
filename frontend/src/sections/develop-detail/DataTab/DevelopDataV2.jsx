import { formatExactMoney } from "src/sections/common/format/money";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  lazy,
  Suspense,
} from "react";
import { AgGridReact } from "ag-grid-react";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
  useEditCellStoreShallow,
  useProcessingStore,
} from "src/sections/develop-detail/states";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { Box, Skeleton } from "@mui/material";
import "./developDataGrid.css";
import {
  getDatasetQueryKey,
  getDatasetQueryOptions,
} from "src/api/develop/develop-detail";
import {
  isCancelledError,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { openAddData } from "src/sections/common/AddData/addDataStore";
import AssignDialog from "src/sections/common/Collab/AssignDialog";
import { commentDeepLink } from "src/sections/common/Collab/commentModel";
import { translateCellDeepLink } from "src/sections/common/Collab/fieldCollabModel";
import { collabSystemColumns } from "./DataGrid/collabColumnDefs";
import { withExpressionColumnDef } from "../AddColumn/Expression/expressionColumnDef";
import { COLLAB_COLUMN } from "./DataGrid/collabColumns";

const COLLAB_COLUMN_IDS = Object.values(COLLAB_COLUMN);
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import {
  columnActionMenuItems,
  DATASET_TYPES,
  DUMMY_ROWS,
  enhanceCol,
  getColumnConfig,
  getDatasetViewOptions,
  getTypeDefinitions,
  onCellValueChangedWrapper,
} from "./common";
import { postProcessDatasetPopup } from "./DataGrid/postProcessDatasetPopup";
import JsonCellEditor from "./DoubleClickEditCell/JsonCellEditor";
import DataTabLayout from "./DataTabLayout";
import { DatasetReadError } from "./DatasetNotFound";
import { defaultRowHeightMapping } from "src/utils/constants";
import {
  useDatasetOriginStore,
  useDevelopCellHeight,
  useDevelopSelectedRowsStore,
  useShowSummaryStoreShallow,
} from "../states";
// Lazy loaded modals/drawers
const EditColumnName = lazy(() => import("./EditColumnName"));
const EditColumnType = lazy(() => import("./EditColumnType"));
const ConfirmDeleteColumn = lazy(() => import("./DeleteColumn"));
const AddEvaluationFeeback = lazy(
  () => import("./AddEvaluationFeeback/AddEvaluationFeeback"),
);
const ImprovePrompt = lazy(() => import("./ImprovePrompt/ImprovePrompt"));
const DoubleClickEditCell = lazy(
  () => import("./DoubleClickEditCell/DoubleClickEditCell"),
);
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import logger from "src/utils/logger";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import DataTabStatusBar from "./DataTabStatusBar";
import { addParamAction, gridViewOnly } from "./dataAccessGate";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { useEvalsList } from "src/sections/common/EvaluationDrawer/getEvalsList";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import SingleImageViewerProvider from "../Common/SingleImageViewer/SingleImageViewerProvider";
import CompositeEvalDialog from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog";
import { MultiImageViewerProvider } from "../Common/MultiImageViewer";
import DevelopFilterBox from "./DevelopFilters/DevelopFilterBox";
import TopBanner from "./TopBanner";
import { transformFilter, validateFilter } from "./DevelopFilters/common";
import useRowViewUrl from "./DatapointDrawerV2/useRowViewUrl";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";
import AddRowData from "./AddRowData";
import DatasetLoader from "../../develop/loaders/DatasetLoader";
import { useEditSyntheticDataStore } from "src/sections/develop/AddRowDrawer/EditSyntheticData/state";
import RunningSkeletonRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/RunningSkeletonRenderer";
import { APP_CONSTANTS } from "src/utils/constants";
import {
  OutputTypes,
  RefreshStatus,
} from "../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
import { enqueueSnackbar } from "notistack";
import { useChangeSetStore } from "./Versions/changeSetStore";
import { pendingRowFromOp, pendingRowsOf } from "./Versions/pendingRows";
import {
  invalidateDatasetSplits,
  useDatasetSplits,
} from "src/api/develop/dataset-splits";
import { isSurfaceShown } from "src/launch/launchSwitch";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridUrlSync,
  useDataGridView,
} from "./DataGrid/useDataGridView";
import { flashRows, takeReveal } from "./DataGrid/newRowsReveal";
import { addColumnDef } from "./DataGrid/AddColumnHeader";
import { rowHeightFor } from "./DataGrid/dataGridView";
import useDataGridLayout from "./DataGrid/useDataGridLayout";
import useAgColumnShaping from "src/sections/common/ux/oneview/useAgColumnShaping";
import { hoverSelectClass } from "src/sections/common/ux/oneview/useHoverSelection";
import {
  RowNumberCell,
  SourceCell,
  SplitsCell,
  UpdatedCell,
  useSourceTraceStore,
} from "./DataGrid/SystemCells";
import SystemFilterPills from "./DataGrid/SystemFilterPills";
import SameOnEveryRowLine from "src/sections/common/Preview/SameOnEveryRowLine";
import { buildCellContextMenu } from "./DataGrid/gridContextMenu";
import { copyGridText } from "./DataGrid/copyGridText";
import { addColumnFilter } from "./DataGrid/gridFilterActions";
import ManageSplitsDialog from "./Splits/ManageSplitsDialog";
import { useManageSplitsStore } from "./Splits/splitStores";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
// Versions seam (L3, PRODUCT-SPEC §3.5): as-of reads, read-only columns,
// change markers and pending cell edits.
import useAsOfGrid from "./Versions/useAsOfGrid";
import { suppressGridTyping } from "src/sections/common/ux/gridKeys"; // R4-F frozen line
import { versionedCellEdit } from "./Versions/versionedCellEdit";
import { opensMediaEditor } from "./DoubleClickEditCell/mediaEditorRoute";
import { whenIdle } from "./whenIdle";
import {
  versionCellClassRules,
  versionCellTooltip,
  versionRowClassRules,
  versionedQueryOptions,
} from "./Versions/versionedSource";
const PdfPreviewDrawer = lazy(() => import("src/components/PdfPreviewDrawer"));
const TraceDetailDrawerV2 = lazy(
  () => import("src/components/traceDetail/TraceDetailDrawerV2"),
);

const getResultColumnConfig = (result) => result?.column_config ?? [];
const getResultIsProcessingData = (result) =>
  Boolean(result?.is_processing_data);
const getResultIsSyntheticDataset = (result) =>
  Boolean(result?.synthetic_dataset);
const getResultSyntheticPercentage = (result) =>
  result?.synthetic_dataset_percentage ?? 100;
const getResultIsSyntheticRegenerate = (result) =>
  Boolean(result?.synthetic_regenerate);
const getRowId = (row) => row?.row_id ?? row?.rowId;
const getColumnSourceId = (column) =>
  column?.source_id !== undefined ? column.source_id : column?.sourceId;
const getColumnOriginType = (column) =>
  column?.origin_type !== undefined ? column.origin_type : column?.originType;
const getColumnIsFrozen = (column) =>
  column?.is_frozen !== undefined ? column.is_frozen : column?.isFrozen;
const getColumnIsVisible = (column) =>
  column?.is_visible !== undefined ? column.is_visible : column?.isVisible;
const SkeletonHeader = () => {
  return <Skeleton width="60%" />;
};

const SelectionHeader = (props) => {
  const onCheckboxClick = (e) => {
    logger.debug("onCheckboxClick", e);
    e.stopPropagation(); // Stop event from reaching the header
    const api = props.api;
    const { selectAll } = api.getServerSideSelectionState();
    if (selectAll) {
      api.setServerSideSelectionState({ selectAll: false, toggledNodes: [] });
    } else {
      api.setServerSideSelectionState({ selectAll: true, toggledNodes: [] });
    }
  };

  return (
    <div className="">
      <div onClick={onCheckboxClick} className=""></div>
    </div>
  );
};

SelectionHeader.propTypes = {
  api: PropTypes.shape({
    getServerSideSelectionState: PropTypes.func.isRequired,
    setServerSideSelectionState: PropTypes.func.isRequired,
  }).isRequired,
};

const selectionColumnDef = {
  pinned: "left",
  lockPinned: true,
  headerComponent: SelectionHeader,
};

// Scenario grids (not versioned) keep the dataset-wide column config; the
// one-view layout shapes only the dataset page's grid.
const NO_GRID = { current: null };

/** The one-view layout owns visibility and freeze (A1-10). */
const withoutLayoutKeys = (def) => {
  // eslint-disable-next-line no-unused-vars
  const { hide, pinned, ...rest } = def;
  return def.children
    ? { ...rest, children: def.children.map(withoutLayoutKeys) }
    : rest;
};

const CustomRowOverlay = ({
  failedToGenerateData,
  setOpenSummaryDrawer,
  gridApiRef,
  updateProcessingSyntheticData,
}) => {
  const { dataset } = useParams();
  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );

  const getStatus = () => {
    const result = tableData?.data?.result;
    if (failedToGenerateData) return "failed-to-generate-synthetic";
    if (getResultIsSyntheticRegenerate(result)) return "regenerating-synthetic";
    return "default-synthetic";
  };

  return (
    <Box
      sx={{
        position: "absolute",
        top: 40,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "background.paper",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        height: "100%",
      }}
    >
      {DatasetLoader({
        status: getStatus(),
        onAction: () => setOpenSummaryDrawer(true),
        gridApiRef,
        isSyntheticDataset: getResultIsSyntheticDataset(
          tableData?.data?.result,
        ),
        updateProcessingSyntheticData,
      })}
    </Box>
  );
};

CustomRowOverlay.propTypes = {
  failedToGenerateData: PropTypes.bool,
  setOpenSummaryDrawer: PropTypes.func,
  gridApiRef: PropTypes.object,
  updateProcessingSyntheticData: PropTypes.func,
};

/**
 * The grid's API filters. The versioned dataset grid adds the toolbar's
 * system filters (Failed only, Splits, Errored cells).
 */
const gridFiltersFor = (datasetId, versioned) =>
  versioned
    ? getDataGridFilters(datasetId)
    : useDevelopFilterStore
        .getState()
        .filters.filter(validateFilter)
        .map(transformFilter);

/** Pending new rows (change-set `add_row`) as table rows with a `New` chip. */
const pendingNewRows = (datasetId) =>
  pendingRowsOf(useChangeSetStore.getState().getOps(datasetId));

export const getDataSource = (
  queryClient,
  datasetId,
  setFailedToGenerateData,
  updateRefreshing,
  updateProcessingSyntheticData,
  overlayTimeoutRef,
  versioned = false,
  onReadError = () => {},
) => {
  let active = true;
  // Only the dataset page reads past versions; scenario grids stay on latest.
  const tableQuery = versioned
    ? versionedQueryOptions
    : (_queryClient, ...args) => getDatasetQueryOptions(...args);
  return {
    destroy: () => {
      active = false;
    },
    getRows: async (params) => {
      const { request } = params;
      const pageNumber = Math.floor(request.startRow / DATASET_ROWS_LIMIT);
      const sort = request?.sortModel?.map(({ colId, sort }) => ({
        column_id: colId,
        type: sort === "asc" ? "ascending" : "descending",
      }));
      const search = useDevelopSearchStore.getState().search;
      const validFilters = gridFiltersFor(datasetId, versioned);
      let tempOrigin;

      if (overlayTimeoutRef.current) {
        clearTimeout(overlayTimeoutRef.current);
        overlayTimeoutRef.current = null;
      }

      try {
        const queryOptions = tableQuery(
          queryClient,
          datasetId,
          pageNumber,
          validFilters,
          sort,
          search,
          { enabled: true, staleTime: 5 * 1000, pageSize: DATASET_ROWS_LIMIT },
        );
        const cachedState = queryClient.getQueryState(queryOptions.queryKey);
        const servedFromCache =
          cachedState?.data !== undefined && !cachedState.isInvalidated;
        const data = servedFromCache
          ? cachedState.data
          : await queryClient.fetchQuery({
              ...queryOptions,
              meta: { ...queryOptions.meta, errorHandled: true },
            });
        const result = data?.data?.result;
        const processingData = getResultIsProcessingData(result);

        useProcessingStore.getState().setIsProcessingData(processingData);

        const rows = processingData ? DUMMY_ROWS : result?.table ?? [];

        const totalRows = processingData
          ? DUMMY_ROWS.length
          : result?.metadata?.total_rows ?? 0;

        tempOrigin = getResultIsSyntheticDataset(result)
          ? DATASET_TYPES["SYNTHETIC_DATASET"]
          : null;
        const columnConfig = getResultColumnConfig(result);

        if (getResultIsSyntheticDataset(result)) {
          const syntheticPercentage = getResultSyntheticPercentage(result);
          params.api.syntheticDatasetPercentage = syntheticPercentage;
          updateProcessingSyntheticData(syntheticPercentage !== 100);
        }
        updateRefreshing(
          columnConfig?.some((v) => RefreshStatus.includes(v?.status)) ||
            processingData,
        );

        // IT-R2 G5 P4: a `system:row_ids` view (e.g. `New (6)` after an
        // import) narrows the pending rows too.
        const onlyIds = versioned ? getDataGridView(datasetId).rowIds : [];
        const pendingRows = (
          versioned && !processingData ? pendingNewRows(datasetId) : []
        ).filter((r) => !onlyIds?.length || onlyIds.includes(getRowId(r)));
        params.api.setGridOption("context", {
          totalRowCount: (totalRows ?? 0) + pendingRows.length,
          filteredRowCount: totalRows ?? 0,
          datasetId,
        });

        if (
          getResultIsSyntheticDataset(result) &&
          getResultSyntheticPercentage(result) !== 100
        ) {
          if (overlayTimeoutRef.current) {
            clearTimeout(overlayTimeoutRef.current);
            overlayTimeoutRef.current = null;
          }

          overlayTimeoutRef.current = setTimeout(() => {
            params.api.showLoadingOverlay();
          }, 100);
        } else {
          if (getResultIsSyntheticDataset(result)) {
            updateProcessingSyntheticData(false);
          }
          const fetchedRows = rows || [];
          const isLastPage = fetchedRows.length < DATASET_ROWS_LIMIT;

          // Pending new rows trail the last block, so server blocks keep
          // their 30-row boundaries; rowCount includes them.
          const atEnd =
            request.startRow + fetchedRows.length >= (totalRows ?? 0);
          params.success({
            rowData:
              atEnd && pendingRows.length
                ? [...fetchedRows, ...pendingRows]
                : fetchedRows,
            rowCount: totalRows + pendingRows.length,
          });
          // IT-R2 G5 P4: the rows an import just added are scrolled to and
          // flashed once.
          if (versioned && atEnd && pendingRows.length) {
            const reveal = takeReveal(datasetId);
            if (reveal) setTimeout(() => flashRows(params.api, reveal), 150);
          }

          // Prefetch the next two pages so scrolling stays ahead of the
          // network and never shows the 2-3s block loader. Pages past the
          // end of the dataset are skipped — e.g. on the second-to-last
          // page only the last page is prefetched, not a non-existent one.
          // A prefetched page is reused only while it is still fresh when
          // getRows consumes it — that is why this and the fetchQuery
          // above use staleTime 5s; with staleTime 0 the page is stale on
          // arrival and getRows fetches it again, wasting the prefetch.
          // perf #6: the first page renders alone; the next pages load when
          // the browser is idle (one page ahead on the first load, two
          // while scrolling), never in the first page's critical path.
          if (!isLastPage) {
            const totalPages = totalRows
              ? Math.ceil(totalRows / DATASET_ROWS_LIMIT)
              : pageNumber + 1;
            const ahead =
              pageNumber === 0
                ? [pageNumber + 1]
                : [pageNumber + 1, pageNumber + 2];
            whenIdle(() =>
              ahead
                .filter((page) => page < totalPages)
                .forEach((page) => {
                  queryClient.prefetchQuery(
                    tableQuery(
                      queryClient,
                      datasetId,
                      page,
                      validFilters,
                      sort,
                      search,
                      {
                        enabled: true,
                        staleTime: 5 * 1000,
                        pageSize: DATASET_ROWS_LIMIT,
                      },
                    ),
                  );
                }),
            );
          }
        }
      } catch (e) {
        // Flow-control errors from request cancellation are not real failures:
        //   • React Query throws CancelledError when a new query supersedes an
        //     in-flight one (common during bulk stop-eval / invalidations).
        //     Its class extends Error but never sets `this.name`, and the
        //     constructor name gets mangled in production — only `instanceof`
        //     (via `isCancelledError`) is reliable.
        //   • Axios throws CanceledError (one L) / code ERR_CANCELED when the
        //     underlying request is aborted before React Query wraps it.
        //   • Fetch / AbortController surfaces AbortError.
        // Don't log or flip the grid into failed state for any of these.
        const err = e;
        const name = err?.name;
        const isCancelled =
          isCancelledError(err) ||
          name === "CanceledError" ||
          name === "AbortError" ||
          err?.code === "ERR_CANCELED";
        if (isCancelled || !active) return;
        onReadError({ datasetId, error: e });
        logger.error("[getRows] failed", {
          message: e instanceof Error ? e.message : String(e),
          stack: e instanceof Error ? e.stack : undefined,
        });
        params.fail();
        if (tempOrigin === DATASET_TYPES["SYNTHETIC_DATASET"]) {
          setFailedToGenerateData(true);
          overlayTimeoutRef.current = setTimeout(() => {
            params.api.showLoadingOverlay();
          }, 100);
        }
      }
    },
  };
};

const onRowSelectionChanged = ({ api, context, source }) => {
  // if (EventToSkip.includes(source)) {
  //   return;
  // }
  logger.debug("onRowSelectionChanged", { api, context, source });
  const totalRowCount = context?.totalRowCount;
  const { selectAll, toggledNodes } = api.getServerSideSelectionState();

  if (selectAll && totalRowCount - toggledNodes.length === 0) {
    api.deselectAll();
  }

  useDevelopSelectedRowsStore.setState({
    toggledNodes: toggledNodes,
    selectAll: selectAll,
  });
};

const onHeaderClicked = ({ api, column, event, ..._rest }) => {
  // Check if click is from checkbox - if so, don't handle header click
  // if (event?.target?.classList?.contains("ag-selection-checkbox")) {
  //   return;
  // }
  // if (column?.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
  //   const { selectAll } = api.getServerSideSelectionState();
  //   if (selectAll) {
  //     api.setServerSideSelectionState({ selectAll: false, toggledNodes: [] });
  //   } else {
  //     api.setServerSideSelectionState({ selectAll: true, toggledNodes: [] });
  //   }
  // }
};

const getDefaultColDefs = () => {
  return [
    {
      headerName: "Column 1",
      field: "name",
      flex: 1,
      headerComponent: SkeletonHeader,
      cellRenderer: RunningSkeletonRenderer,
      id: 1,
    },
    {
      headerName: "Column 2",
      field: "numberOfDatapoints",
      flex: 1,
      headerComponent: SkeletonHeader,
      cellRenderer: RunningSkeletonRenderer,
      id: 2,
    },
    {
      headerName: "Column 3",
      field: "numberOfExperiments",
      flex: 1,
      headerComponent: SkeletonHeader,
      cellRenderer: RunningSkeletonRenderer,
      id: 3,
    },
    {
      headerName: "Column 4",
      field: "numberOfOptimisations",
      flex: 1,
      headerComponent: SkeletonHeader,
      cellRenderer: RunningSkeletonRenderer,
      id: 4,
    },
  ];
};

const AVERAGE_ROW_ID = "__average__";

const SystemHeader = ({ displayName }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      height: "100%",
      px: 1,
      typography: "s2",
      fontWeight: "fontWeightMedium",
      color: "text.secondary",
    }}
  >
    {displayName}
  </Box>
);
SystemHeader.propTypes = { displayName: PropTypes.string };

/**
 * Versioned grid columns (area-datasets G1, G2): `Row` pinned after the
 * checkbox, `Splits` next to it, the dataset's columns, then `Source` and
 * `Updated`. Wrap density turns on auto height for every data column.
 */
const withSystemColumns = (
  columnMap,
  { dataset, hasSplits, wrap, collab = [] },
) => {
  const params = { datasetId: dataset };
  const system = (field, headerName, extra) => ({
    colId: field,
    field,
    headerName,
    headerComponent: SystemHeader,
    editable: false,
    suppressHeaderMenuButton: true,
    cellRendererParams: params,
    lockPinned: true,
    ...extra,
  });
  // AG Grid measures auto-height cells from their content, so the cell
  // must not stretch to 100% of the row.
  const autoHeight = {
    autoHeight: true,
    wrapText: true,
    cellStyle: { padding: 0, display: "flex", flexDirection: "column" },
  };
  const dataCols = columnMap.map(withoutLayoutKeys).map((c) =>
    wrap
      ? {
          ...c,
          ...autoHeight,
          ...(c.children && {
            children: c.children.map((ch) => ({ ...ch, ...autoHeight })),
          }),
        }
      : c,
  );
  return [
    system("system:order", "Row", {
      width: 72,
      minWidth: 72,
      maxWidth: 72,
      pinned: "left",
      sortable: true,
      resizable: false,
      cellRenderer: RowNumberCell,
      headerComponent: undefined,
      comparator: () => 0,
    }),
    ...(hasSplits
      ? [
          system("system:splits", "Splits", {
            width: 170,
            sortable: false,
            cellRenderer: SplitsCell,
          }),
        ]
      : []),
    ...dataCols,
    system("system:source", "Source", {
      width: 160,
      sortable: false,
      cellRenderer: SourceCell,
    }),
    system("system:updated", "Updated", {
      width: 150,
      sortable: false,
      cellRenderer: UpdatedCell,
    }),
    ...collabSystemColumns(system, collab),
  ];
};

export const getAverageColumnConfig = (columns, tableRows) => {
  if (!columns?.length) {
    return [];
  }
  const grouping = {};

  const bottomRow = {};

  const firstRow = tableRows?.[0];

  for (const eachCol of columns) {
    const sourceId = getColumnSourceId(eachCol);
    const originType = getColumnOriginType(eachCol);
    if (
      sourceId &&
      (originType === "evaluation" || originType === "evaluation_reason")
    ) {
      if (!grouping[sourceId]) {
        grouping[sourceId] = [eachCol];
      } else {
        grouping[sourceId].push(eachCol);
      }
    } else {
      grouping[eachCol?.id] = [eachCol];
    }
  }

  for (const [_, cols] of Object.entries(grouping)) {
    // Ensure evaluation columns come before evaluation_reason so we
    // pick the result column (which carries average_score) as cols[0].
    if (cols.length > 1) {
      cols.sort((a, b) => {
        const aType = a.originType || a.origin_type || "";
        const bType = b.originType || b.origin_type || "";
        if (aType === "evaluation" && bType !== "evaluation") return -1;
        if (bType === "evaluation" && aType !== "evaluation") return 1;
        return 0;
      });
    }

    if (cols.length === 1) {
      const eachCol = cols[0];

      const isNumericOutput =
        firstRow?.[eachCol.id]?.value_infos?.output === OutputTypes.NUMERIC;

      bottomRow[eachCol.id] =
        eachCol?.average_score === 0 || eachCol?.average_score ? (
          isNumericOutput ? (
            `Average : ${eachCol?.average_score}`
          ) : (
            `Average : ${eachCol?.average_score}%`
          )
        ) : eachCol?.metadata?.average_cost ||
          eachCol?.metadata?.average_latency ||
          eachCol?.metadata?.average_tokens ? (
          <div
            style={{
              display: "flex",
              gap: 2,
              lineHeight: "1.5",
              alignItems: "center",
            }}
          >
            <Iconify
              icon="solar:clock-circle-linear"
              sx={{
                display: "flex",
                gap: 0.5,
                alignItems: "center",
                width: 12,
                height: 12,
                marginRight: "2px",
              }}
            />
            {Math.round(eachCol?.metadata?.average_latency)}ms
            <SvgColor
              src="/assets/icons/components/ic_coin.svg"
              sx={{
                width: 15,
                height: 15,
                marginLeft: "16px",
                marginRight: "2px",
                color: "text.primary",
              }}
            />
            {Math.round(eachCol?.metadata?.average_tokens)}
            <Iconify
              icon="solar:dollar-minimalistic-linear"
              sx={{
                display: "flex",
                alignItems: "center",
                width: 12,
                height: 12,
                marginLeft: "16px",
                marginRight: "-2px",
              }}
            />
            {formatExactMoney(eachCol?.metadata?.average_cost, {
              prefix: "",
              minimumFractionDigits: 6,
            })}
          </div>
        ) : (
          ""
        );
    } else {
      const eachCol = cols[0];

      bottomRow[eachCol.id] =
        eachCol?.average_score === 0 || eachCol?.average_score
          ? firstRow?.[eachCol.id]?.value_infos?.output === "numeric"
            ? `Average : ${eachCol?.average_score}`
            : `Average : ${eachCol?.average_score}%`
          : "";
    }
  }

  // Skip the pinned summary row when no column has a value to show; otherwise
  // every cell is "" and AG Grid renders a blank placeholder row at the bottom.
  if (!Object.values(bottomRow).some(Boolean)) {
    return [];
  }

  return [
    {
      // AG Grid calls getRowId for pinned rows too; give it a stable id.
      row_id: AVERAGE_ROW_ID,
      checkbox: "",
      ...bottomRow,
    },
  ];
};
const DATASET_ROWS_LIMIT = 30;
const BATCH_SIZE = 3;
// Versioned grid: 52 px two-line headers (name, then role / stats).
const VERSIONED_THEME = { columnBorder: true, headerHeight: "52px" };
const VERSIONED_GRID_CONTAINER = { flex: "1 1 0", minHeight: 0 };

const DevelopDataV2 = ({
  datasetId,
  viewOptions,
  versioned: versionedProp,
}) => {
  const { role } = useAuthContext();
  const roleViewer = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const gridApiRef = useRef(null);
  const agTheme = useAgThemeWith(
    versionedProp ?? !datasetId ? VERSIONED_THEME : AG_THEME_OVERRIDES.dataGrid,
  );
  const queryClient = useQueryClient();
  const { dataset: datasetFromParams } = useParams();
  const _viewOptions = getDatasetViewOptions(viewOptions);

  const dataset = datasetId || datasetFromParams;
  // v2.1 g8 (access:gates): a member whose access to this dataset is View
  // gets the viewer grid (no cell edit, no column menu writes, no `+`
  // column), whatever their workspace role. The server's guard refuses too.
  const access = useDatasetAccess(dataset);
  const isViewerRole = gridViewOnly({ roleViewer, can: access.can });
  // R7: the dataset page's grid is versioned (row numbers, splits, header
  // stats, pending edits). Scenario grids pass `datasetId` and stay classic.
  const versioned = versionedProp ?? !datasetId;
  const showSummary = useShowSummaryStoreShallow((s) => s.showSummary);
  const { rowId: openRowId, openRow } = useRowViewUrl();
  const gridView = useDataGridView(dataset);
  // One view (A1-10): the server config lists the columns; this user's
  // layout shows / hides / freezes / orders them (the toolbar's View menu
  // and the header menu edit the same layout).
  const layout = useDataGridLayout(dataset, {
    commands: false,
    enabled: versioned,
  });
  const shaping = useAgColumnShaping({
    gridRef: versioned ? gridApiRef : NO_GRID,
    layout,
  });
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  // HO D4: the optional Row info columns the catalog offers.
  const collabColumnIds = useMemo(
    () =>
      (layout?.catalog ?? [])
        .map((c) => c.id)
        .filter((id) => COLLAB_COLUMN_IDS.includes(id)),
    [layout?.catalog],
  );
  const hasSelection = useDevelopSelectedRowsStore(
    (s) => s.selectAll || s.toggledNodes.length > 0,
  );
  const { data: splitData } = useDatasetSplits(dataset, {
    enabled: versioned && isSurfaceShown("dataset.splits"),
  });
  useDataGridUrlSync(dataset, splitData?.splits, { enabled: versioned });
  const sourceTrace = useSourceTraceStore((s) => s.trace);
  const closeSourceTrace = useSourceTraceStore((s) => s.close);
  const setManageSplitsOpen = useManageSplitsStore((s) => s.setOpen);
  const [queueTarget, setQueueTarget] = useState(null);
  // W4 §9 (M6): the cell menu's Assign row to… and Comment on row.
  const [assignRowId, setAssignRowId] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // v2.1 g8 (create:from-production, header:more): `add=production` (the
  // header's Add production failures, a shared link) opens the production
  // sheet on this dataset, then leaves the URL. Viewers get the refusal.
  const addParam = searchParams.get("add");
  useEffect(() => {
    const act = addParamAction({
      addParam,
      datasetId: dataset,
      can: access.can,
      roleViewer,
      loading: access.isLoading,
    });
    if (!act || act.kind === "wait") return;
    const next = new URLSearchParams(window.location.search);
    next.delete("add");
    setSearchParams(next, { replace: true });
    if (act.kind === "refuse") {
      enqueueSnackbar(
        access.reason("edit") || "You can't add rows to this dataset.",
        { variant: "info" },
      );
      return;
    }
    openAddData(act.request);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addParam, dataset, access.isLoading]);
  const canAssign = !isViewerRole && access.can.edit;
  const accessReason = access.reason;
  const lastPointer = useRef({ x: 0, y: 0 });
  const rowOpenTimer = useRef(null);
  const lastCellClick = useRef({ key: null, at: 0 });
  const pointerDownAt = useRef(0);
  const openRowRef = useRef(openRowId);
  openRowRef.current = openRowId;
  const focusFollowTimer = useRef(null);

  // Warm the saved-evals list cache for this dataset so per-cell renderers
  // (CustomCellRender) and the datapoint drawer can synchronously look up
  // the eval_type for an eval column. This is the same query key the
  // EvaluationDrawer uses, so the request is deduped.
  useEvalsList(
    _viewOptions.showEvals ? dataset : undefined,
    { eval_type: "user" },
    "dataset",
  );
  const isRefreshingColumns = useRef(false);
  const { setGridApi, setRefetchTable } = useDevelopDetailContext();
  const setEditCell = useEditCellStoreShallow((s) => s.setEditCell);
  const performedClicks = useRef(0);
  const clickTimeout = useRef(null);
  const isProcessingSyntheticData = useRef(false);
  const { processingComplete, setProcessingComplete } = useDatasetOriginStore();
  const overlayTimeoutRef = useRef(null);
  const wasProcessingData = useRef(false);

  const updateProcessingSyntheticData = useCallback(
    (val) => {
      logger.debug({ isProcessingSyntheticData: val });
      isProcessingSyntheticData.current = val;

      setProcessingComplete(!val);
    },
    [setProcessingComplete],
  );

  const updateRefreshing = useCallback((val) => {
    isRefreshingColumns.current = val;
  }, []);

  const cellHeight = useDevelopCellHeight((s) => s.cellHeight);
  const {
    setOpenSummaryDrawer,
    failedToGenerateData,
    setFailedToGenerateData,
  } = useEditSyntheticDataStore();

  // Grid Options
  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: true,
      cellStyle: {
        padding: 0,
        height: "100%",
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
      cellClassRules: versionCellClassRules,
      tooltipValueGetter: versionCellTooltip,
      // R4 D25 (was G9 / MUST 6 / DG#30, versioned grid only): in every grid
      // mode, while no cell is being edited every printable key, Enter and
      // Space are shortcuts (they bubble to useShortcuts; `?` opens the
      // sheet), never "start typing". Editing: F2, double-click, Edit.
      suppressKeyboardEvent: suppressGridTyping, // R4-F frozen line
    }),
    [],
  );
  isProcessingSyntheticData;
  const [readFailure, setReadFailure] = useState(null);

  // Reset row selection when dataset changes or component unmounts (tab switch)
  useEffect(() => {
    useDevelopSelectedRowsStore.getState().setToggledNodes([]);
    useDevelopSelectedRowsStore.getState().setSelectAll(false);
    gridApiRef.current?.api?.deselectAll();
    return () => {
      useDevelopSelectedRowsStore.getState().setToggledNodes([]);
      useDevelopSelectedRowsStore.getState().setSelectAll(false);
    };
  }, [dataset]);

  useEffect(() => {
    const dataSource = getDataSource(
      queryClient,
      dataset,
      setFailedToGenerateData,
      updateRefreshing,
      updateProcessingSyntheticData,
      overlayTimeoutRef,
      versioned,
      setReadFailure,
    );
    gridApiRef.current?.api?.setGridOption("serverSideDatasource", dataSource);
  }, [
    dataset,
    datasetId,
    queryClient,
    setFailedToGenerateData,
    updateRefreshing,
    updateProcessingSyntheticData,
    versioned,
  ]);

  const onGridReady = useCallback(
    (params) => {
      const dataSource = getDataSource(
        queryClient,
        dataset,
        setFailedToGenerateData,
        updateRefreshing,
        updateProcessingSyntheticData,
        overlayTimeoutRef,
        versioned,
        setReadFailure,
      );
      params.api.setGridOption("serverSideDatasource", dataSource);
      params.api.setGridOption("onSelectionChanged", onRowSelectionChanged);
      params.api.setGridOption("onColumnHeaderClicked", onHeaderClicked);
    },
    [
      dataset,
      datasetId,
      queryClient,
      setFailedToGenerateData,
      updateRefreshing,
      updateProcessingSyntheticData,
      versioned,
    ],
  );

  const {
    data: tableData,
    isPending: isLoadingTable,
    refetch: refetchTableData,
  } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", {
      enabled: false,
    }),
  );

  // send refetch to provider
  useEffect(() => {
    if (refetchTableData) {
      setRefetchTable(refetchTableData);
    }
  }, [refetchTableData, setRefetchTable]);

  const { data: averageMetaData, refetch: refreshAverage } = useQuery({
    queryKey: ["dataset-detail-average", dataset],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(dataset), {
        params: {
          column_config_only: true,
        },
      }),
    select: (d) => ({
      columnConfig:
        d.data?.result?.column_config ?? d.data?.result?.columnConfig,
      isProcessingData: Boolean(
        d.data?.result?.is_processing_data ?? d.data?.result?.isProcessingData,
      ),
    }),
    staleTime: Infinity,
  });

  const { mutate: updateDataset } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
  });

  const bottomRow = useMemo(() => {
    return getAverageColumnConfig(
      averageMetaData?.columnConfig,
      tableData?.data?.result?.table,
    );
  }, [averageMetaData?.columnConfig, tableData?.data?.result?.table]);

  const dataTypeDefinitions = useMemo(getTypeDefinitions, []);

  const { data: datasetList } = useDevelopDatasetList();

  const currentDataset = datasetList?.find((v) => v.datasetId === dataset);

  const isSyntheticDataset = getResultIsSyntheticDataset(
    tableData?.data?.result,
  );

  const statusBar = useMemo(() => {
    return isSyntheticDataset && !processingComplete
      ? undefined
      : {
          statusPanels: [
            {
              statusPanel: DataTabStatusBar,
              statusPanelParams: { datasetId: dataset, versioned },
              align: "left",
              key: "rowCounter",
            },
          ],
        };
  }, [isSyntheticDataset, processingComplete, dataset, versioned]);

  const asOf = useAsOfGrid({ versioned });
  const columnConfig = useMemo(() => {
    if (averageMetaData?.isProcessingData) {
      return averageMetaData?.columnConfig ?? [];
    }
    const tc = getResultColumnConfig(tableData?.data?.result);
    return asOf.columnsFor(
      tc?.length ? tc : averageMetaData?.columnConfig ?? [],
    );
  }, [
    tableData?.data?.result?.column_config,
    averageMetaData?.columnConfig,
    averageMetaData?.isProcessingData,
    asOf,
  ]);

  const {
    clearWaveSurferCache,
    getWaveSurferInstance,
    removeWaveSurferInstance,
    storeWaveSurferInstance,
    updateWaveSurferInstance,
  } = useWavesurferCache();

  useEffect(() => {
    // Return a cleanup function to clear cache on component unmount
    return () => {
      clearWaveSurferCache();
    };
  }, [clearWaveSurferCache]); // Dependency array includes the stable callback

  // The dataset page versions hand edits; scenario grids (datasetId prop)
  // keep writing immediately.
  const onCellValueChanged = datasetId
    ? onCellValueChangedWrapper(queryClient, dataset)
    : versionedCellEdit({
        datasetId: dataset,
        fallback: onCellValueChangedWrapper(queryClient, dataset),
        // v2.1 g8 (stage:multimodal-data): a media save on the dataset page
        // is one pending change, never also an immediate update_cell_value.
        mediaPending: true,
      });

  const columnDefs = useMemo(() => {
    if (isLoadingTable || averageMetaData?.isProcessingData) {
      return getDefaultColDefs();
    }
    const grouping = {};
    for (const eachCol of columnConfig) {
      const sourceId = getColumnSourceId(eachCol);
      const originType = getColumnOriginType(eachCol);
      if (
        sourceId &&
        (originType === "evaluation" || originType === "evaluation_reason")
      ) {
        if (!grouping[sourceId]) {
          grouping[sourceId] = [eachCol];
        } else {
          grouping[sourceId].push(eachCol);
        }
      } else {
        grouping[eachCol?.id] = [eachCol];
      }
    }

    // Ensure evaluation columns come before evaluation_reason in each
    // group so the result renders by default (not the reason).
    for (const key of Object.keys(grouping)) {
      const grp = grouping[key];
      if (grp.length > 1) {
        grp.sort((a, b) => {
          const aType = a.originType || a.origin_type || "";
          const bType = b.originType || b.origin_type || "";
          if (aType === "evaluation" && bType !== "evaluation") return -1;
          if (bType === "evaluation" && aType !== "evaluation") return 1;
          return 0;
        });
      }
    }

    const columnMap = [];

    for (const [_, cols] of Object.entries(grouping)) {
      if (cols.length === 1) {
        const eachCol = enhanceCol(cols[0], averageMetaData?.columnConfig);
        columnMap.push(
          getColumnConfig({
            eachCol,
            dataset,
            queryClient,
            getWaveSurferInstance,
            storeWaveSurferInstance,
            removeWaveSurferInstance,
            updateWaveSurferInstance,
            isViewerRole,
            versioned,
            layout,
          }),
        );
      } else {
        let eachCol = enhanceCol(cols[0], averageMetaData?.columnConfig);
        let children = null;
        // showSummary is keyed by sourceId (the stable eval binding id),
        // not by the ag-grid column id — the column id would otherwise flip
        // between the evaluation / evaluation_reason child column ids once
        // the grouping is applied.
        const groupKey = getColumnSourceId(eachCol) || eachCol.id;
        if (showSummary.includes(groupKey)) {
          eachCol = enhanceCol(
            cols.find((v) => getColumnOriginType(v) === "evaluation"),
            averageMetaData?.columnConfig,
          );
          children = cols.map((v) =>
            getColumnConfig({
              eachCol: v,
              dataset,
              queryClient,
              getWaveSurferInstance,
              storeWaveSurferInstance,
              removeWaveSurferInstance,
              updateWaveSurferInstance,
              isViewerRole,
              versioned,
              layout,
            }),
          );
        }
        logger.debug({ children });
        columnMap.push(
          getColumnConfig({
            eachCol,
            ...(children && { children }),
            dataset,
            queryClient,
            getWaveSurferInstance,
            storeWaveSurferInstance,
            removeWaveSurferInstance,
            updateWaveSurferInstance,
            isViewerRole,
            versioned,
            layout,
          }),
        );
      }
    }
    const locked = asOf.lockColumns(columnMap);
    // HO D7: an Expression column is computed (not edited in place) and
    // its header menu opens its own form.
    if (!versioned) return locked.map(withExpressionColumnDef);
    // One menu per column (A1-10): the column's own actions, then the
    // one-view header items (Sort, Freeze, Move, Hide, Columns…, Reset),
    // then AG's autosize.
    const menuFor = (def) => ({
      ...def,
      mainMenuItems: (params) => {
        const id = params.column?.getColId?.();
        const known = layoutRef.current.catalog.some((c) => c.id === id);
        if (!known && typeof def.mainMenuItems === "function")
          return def.mainMenuItems(params);
        const own = columnActionMenuItems(
          params.column?.getColDef?.()?.col,
          isViewerRole,
          {
            versioned,
            datasetId: dataset,
            queryClient,
          },
        ).filter((i, k, all) => i !== "separator" || k < all.length - 1);
        return [
          ...own,
          ...(own.length ? ["separator"] : []),
          ...shaping.getMainMenuItems(params),
          "separator",
          "autoSizeThis",
          "autoSizeAll",
        ];
      },
      ...(def.children && { children: def.children.map(menuFor) }),
    });
    return [
      ...withSystemColumns(locked.map(menuFor).map(withExpressionColumnDef), {
        dataset,
        hasSplits: (splitData?.splits?.length ?? 0) > 0,
        wrap: rowHeightFor(gridView.density) == null,
        collab: collabColumnIds,
      }),
      // IT-R2 G5 P5: `+` at the end of the header → Add column from path.
      ...(isViewerRole ? [] : [addColumnDef(dataset)]),
    ];
  }, [
    asOf,
    columnConfig,
    averageMetaData?.columnConfig,
    showSummary,
    isLoadingTable,
    getWaveSurferInstance,
    storeWaveSurferInstance,
    removeWaveSurferInstance,
    updateWaveSurferInstance,
    dataset,
    queryClient,
    averageMetaData?.isProcessingData,
    versioned,
    gridView.density,
    splitData?.splits?.length,
    isViewerRole,
    shaping,
    layout,
    collabColumnIds,
  ]);

  const isData = useMemo(() => {
    const isSdk = Boolean(tableData?.data?.result?.dataset_config?.is_sdk);
    if (tableData === undefined) return true;
    return Boolean(!isSdk || tableData?.data?.result?.table.length);
  }, [tableData]);

  const refreshRowsManual = useCallback(async () => {
    // The 5s polling interval can fire after the grid has been unmounted
    // (navigation / tab switch), leaving gridApiRef.current null. Bail out
    // instead of dereferencing .api -> 'Cannot read properties of null'.
    if (!gridApiRef.current?.api) return;
    const cacheBlockState = gridApiRef?.current?.api?.getCacheBlockState();

    // Get only loaded/active blocks
    const activePages = Object?.entries(cacheBlockState || {})
      .filter(([_, state]) => state?.pageStatus === "loaded")
      .map(([page]) => parseInt(page));

    // Get the currently visible page
    const firstDisplayedRow =
      gridApiRef.current.api.getFirstDisplayedRowIndex();
    const pageSize = DATASET_ROWS_LIMIT;
    const currentPage = Math.floor(firstDisplayedRow / pageSize);

    // Refresh current page + adjacent pages (only those that are loaded)
    let pagesToRefresh = [
      Math.max(0, currentPage - 1),
      currentPage,
      currentPage + 1,
    ].filter((p) => activePages.includes(p));

    // If no pages to refresh, we can't exit early coz we need to get percentage for synthetic data
    if (pagesToRefresh.length === 0) {
      pagesToRefresh = [0];
    }

    const columnState = gridApiRef.current.api.getColumnState();

    const sort = columnState.reduce((acc, { colId, sort }) => {
      if (sort) {
        acc.push({
          column_id: colId,
          type: sort === "asc" ? "ascending" : "descending",
        });
      }
      return acc;
    }, []);

    const search = useDevelopSearchStore.getState().search;
    const validFilters = gridFiltersFor(dataset, versioned);

    const maxActivePage = activePages.length
      ? Math.max(...activePages)
      : currentPage;
    const totalRowCount =
      gridApiRef?.current?.api?.getGridOption("context")?.totalRowCount;
    const lastPage = totalRowCount
      ? Math.ceil(totalRowCount / DATASET_ROWS_LIMIT) - 1
      : maxActivePage;
    const endPage = Math.min(maxActivePage + 2, lastPage);
    for (let p = Math.max(0, currentPage - 1); p <= endPage; p++) {
      const { queryKey } = getDatasetQueryOptions(
        dataset,
        p,
        validFilters,
        sort,
        search,
        {},
      );
      queryClient.invalidateQueries({ queryKey });
    }

    // Process pages in batches of 3 to avoid overwhelming the server
    const totalPages = pagesToRefresh.length;

    for (let i = 0; i < totalPages; i += BATCH_SIZE) {
      try {
        const currentBatchPages = Array.from(
          new Set(
            pagesToRefresh.slice(i, Math.min(i + BATCH_SIZE, totalPages)),
          ),
        );

        for (const p of currentBatchPages) {
          const queryOptions = getDatasetQueryOptions(
            dataset,
            p,
            validFilters,
            sort,
            search,
            {},
          );
          queryClient.invalidateQueries({
            queryKey: queryOptions.queryKey,
          });
          const data = await queryOptions.queryFn();
          const result = data?.data?.result;
          const processingData = getResultIsProcessingData(result);
          const rows = processingData ? DUMMY_ROWS : result?.table;

          const columnConfig = processingData
            ? getDefaultColDefs()
            : getResultColumnConfig(result);

          if (getResultIsSyntheticDataset(result)) {
            const initialState = isProcessingSyntheticData.current;
            isProcessingSyntheticData.current =
              getResultSyntheticPercentage(result) !== 100;

            gridApiRef.current.api.syntheticDatasetPercentage =
              getResultSyntheticPercentage(result);
            // Need to one complete refresh coz there are no total rows set need to be set
            if (initialState !== isProcessingSyntheticData.current) {
              gridApiRef.current.api.refreshServerSide();
              return;
            }
          }

          isRefreshingColumns.current = columnConfig?.some((v) =>
            RefreshStatus.includes(v?.status),
          );

          if (!isRefreshingColumns.current) {
            refreshAverage();
          }

          // The grid still shows the processing placeholders, which an
          // update transaction cannot replace (their ids match no real row).
          // The refresh fired when processing ended may have been answered
          // by a page request sent before it ended, so reload the grid from
          // this response instead.
          if (
            !processingData &&
            useProcessingStore.getState().isProcessingData
          ) {
            queryClient.setQueryData(queryOptions.queryKey, data);
            gridApiRef.current.api.refreshServerSide();
            continue;
          }

          if (rows?.length === 0) {
            gridApiRef.current?.api.refreshServerSide({ purge: true });
            gridApiRef.current?.api.setGridOption("context", {
              totalRowCount: 0,
            });
          }
          const transaction = {
            update: rows,
          };

          // if we are getting processing percentage for synthetic data we don't to put data in the grid hence returning early
          if (
            getResultIsSyntheticDataset(result) &&
            isProcessingSyntheticData.current === true
          ) {
            return;
          }

          gridApiRef.current.api.applyServerSideTransaction(transaction);
        }
      } catch (e) {
        // Surface enough context to distinguish this error from other
        // AG-Grid spam when `debug` is enabled — tagged so a
        // "refreshRowsManual" console filter finds it.
        logger.error("[refreshRowsManual] failed", {
          dataset,
          message: e instanceof Error ? e.message : String(e),
          stack: e instanceof Error ? e.stack : undefined,
        });
      }
    }
  }, [dataset, queryClient, refreshAverage, versioned]);

  useEffect(() => {
    const interval = setInterval(() => {
      if (
        isRefreshingColumns.current ||
        isProcessingSyntheticData.current ||
        averageMetaData?.isProcessingData
      ) {
        refreshRowsManual();
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [averageMetaData?.isProcessingData, refreshRowsManual]);

  useEffect(() => {
    if (wasProcessingData.current && !averageMetaData?.isProcessingData) {
      // The cached pages were read while processing (the filter box's
      // observer refetches them on every poll), and getRows serves any
      // page that is not invalidated, so mark them stale before reloading.
      queryClient.invalidateQueries({ queryKey: ["dataset-detail", dataset] });
      gridApiRef.current?.api?.refreshServerSide();
    }
    wasProcessingData.current = Boolean(averageMetaData?.isProcessingData);
  }, [
    averageMetaData?.isProcessingData,
    refetchTableData,
    queryClient,
    dataset,
  ]);

  const onColumnChanged = useCallback(
    (params) => {
      if (
        (!params.finished && params.type === "columnMoved") ||
        params.source === "gridOptionsChanged"
      ) {
        return;
      }

      // System columns (Row, Splits, Source, Updated) are view-only: their
      // visibility lives in localStorage, never in the dataset's column config.
      const newColumnOrder = params.api
        .getColumnState()
        .filter(
          ({ colId }) =>
            colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN &&
            !String(colId).startsWith("system:"),
        );

      queryClient.setQueryData(
        getDatasetQueryKey(dataset, 0, [], [], ""),
        (oldData) => {
          const existingColumnOrder = getResultColumnConfig(
            oldData?.data?.result,
          );
          const colDefMap = existingColumnOrder.reduce((acc, col) => {
            acc[col.id] = col;
            return acc;
          }, {});
          const newState = newColumnOrder.map((state) => {
            const pinned =
              params.type === "columnPinned" &&
              params.column?.colId === state.colId
                ? params?.pinned
                : getColumnIsFrozen(colDefMap[state.colId]);

            const visible =
              params.type === "columnVisible" &&
              params.column?.colId === state.colId
                ? params.visible
                : getColumnIsVisible(colDefMap[state.colId]);

            return {
              ...colDefMap[state.colId],
              is_frozen: pinned,
              is_visible: visible,
            };
          });

          return {
            ...oldData,
            data: {
              ...oldData.data,
              result: { ...oldData.data.result, column_config: newState },
            },
          };
        },
      );

      if (params?.type === "columnPinned" && params?.pinned) {
        if (params?.pinned === "left") {
          trackEvent(Events.columnPinnedClicked, {
            [PropertyName.status]: "pin left",
          });
        } else if (params?.pinned === "right") {
          trackEvent(Events.columnPinnedClicked, {
            [PropertyName.status]: "pin right",
          });
        }
      }

      const filteredColumnOrder = [];
      const columnConfig = {};
      const hiddenColumns = [];

      for (const column of newColumnOrder) {
        if (column.colId !== "checkbox") {
          filteredColumnOrder.push(column.colId);
          columnConfig[column.colId] = {
            is_visible: !column.hide,
            is_frozen: column?.pinned,
          };
          if (column.hide) {
            const columnDef = params.api.getColumnDef(column.colId); // Get column definition
            const columnName =
              columnDef?.headerName || columnDef?.field || column.colId; // Get column name
            hiddenColumns.push(columnName);
          }
        }
      }
      trackEvent(Events.columnDeselectionSuccessful, {
        dataset_name: currentDataset?.name,
        deselected_columms_name: hiddenColumns,
      });

      // @ts-ignore
      updateDataset({
        dataset_name: currentDataset?.name,
        column_order: filteredColumnOrder,
        column_config: columnConfig,
      });
    },
    [currentDataset, queryClient, updateDataset, dataset],
  );

  const doubleClickCellEdit = useCallback(
    (event) => {
      if (isViewerRole) return;
      // Every media type opens its own editor (integration C1): a filled PDF opens
      // the file dialog, not a raw-URL textarea; images open EditImages.
      if (
        opensMediaEditor(event, {
          selectionColumnId: APP_CONSTANTS.AG_GRID_SELECTION_COLUMN,
        })
      ) {
        setEditCell({ ...event });
      }
    },
    [setEditCell, isViewerRole],
  );

  const debounceCellClick = (handler, event, delay = 250) => {
    performedClicks.current++;
    clickTimeout.current = setTimeout(() => {
      if (performedClicks.current === 1) {
        performedClicks.current = 0;
        handler(event);
      } else {
        performedClicks.current = 0;
      }
    }, delay);
    if (performedClicks.current > 1 && clickTimeout.current) {
      clearTimeout(clickTimeout.current);
    }
  };

  useEffect(() => {
    return () => {
      if (overlayTimeoutRef.current) {
        clearTimeout(overlayTimeoutRef.current);
        overlayTimeoutRef.current = null;
      }
      clearTimeout(rowOpenTimer.current);
      clearTimeout(focusFollowTimer.current);
    };
  }, []);

  // Pending edits repaint their cells; added / deleted rows reload page 0.
  useEffect(() => {
    if (!versioned) return undefined;
    let prev = useChangeSetStore.getState().getOps(dataset);
    return useChangeSetStore.subscribe((state) => {
      const ops = state.sets[dataset]?.ops ?? [];
      if (ops === prev) return;
      const rowsShape = (list) =>
        JSON.stringify(
          list
            .filter((o) => o.op === "add_row" || o.op === "delete_rows")
            .map((o) => [o.op, o.temp_id, o.row_ids]),
        );
      const reload = rowsShape(ops) !== rowsShape(prev);
      // Values typed into a pending new row change its op, not the grid's
      // copy of the row: hand the row node the rebuilt row.
      const typedNew = reload
        ? []
        : ops.filter((o) => o.op === "add_row" && !prev.includes(o));
      prev = ops;
      const api = gridApiRef.current?.api;
      if (!api || api.isDestroyed?.()) return;
      if (reload) api.refreshServerSide({ purge: false });
      typedNew.forEach((o) =>
        api.getRowNode?.(o.temp_id)?.setData?.(pendingRowFromOp(o)),
      );
      api.redrawRows();
    });
  }, [versioned, dataset]);

  // Toolbar toggles (Failed only, Splits, Errored cells) and the R2 system
  // pills (Rows, Source, Project, Invalid rows only) re-query the grid.
  const systemFilterKey = versioned
    ? JSON.stringify([
        gridView.failedOnly,
        gridView.failedEvalIds ?? [],
        gridView.splitIds,
        gridView.cellErrorOnly,
        gridView.rowIds ?? [],
        gridView.sourceKind ?? null,
        gridView.sourceProject ?? null,
        gridView.invalidOnly ?? false,
        gridView.hydratedAt ?? 0,
      ])
    : "";
  const firstFilterKey = useRef(true);
  useEffect(() => {
    if (!versioned) return;
    if (firstFilterKey.current) {
      firstFilterKey.current = false;
      return;
    }
    gridApiRef.current?.api?.onFilterChanged();
  }, [versioned, systemFilterKey]);

  // Density: new fixed height, or auto height for Wrap.
  const rowHeight = versioned
    ? rowHeightFor(gridView.density) ?? undefined
    : defaultRowHeightMapping[cellHeight]?.height;
  useEffect(() => {
    if (!versioned) return;
    const api = gridApiRef.current?.api;
    if (!api) return;
    api.resetRowHeights();
    api.refreshCells({ force: true });
  }, [versioned, gridView.density]);

  // The open row is highlighted.
  const prevOpenRow = useRef(null);
  useEffect(() => {
    if (!versioned) return;
    const api = gridApiRef.current?.api;
    if (!api) return;
    const nodes = [prevOpenRow.current, openRowId]
      .map((id) => (id ? api.getRowNode(id) : null))
      .filter(Boolean);
    prevOpenRow.current = openRowId;
    if (nodes.length) api.redrawRows({ rowNodes: nodes });
  }, [versioned, openRowId]);

  const openRowFromGrid = useCallback(
    (id, opts) => {
      if (!id) return;
      openRow(id, opts);
    },
    [openRow],
  );

  const contextMenuItems = useCallback(
    (params) => {
      if (!versioned) return params.defaultItems;
      return buildCellContextMenu(params, {
        splits: splitData?.splits ?? [],
        ops: useChangeSetStore.getState().getOps(dataset),
        handlers: {
          filterTo: (colId, value, colDef) =>
            addColumnFilter(params.api, {
              columnId: colId,
              filterType:
                colDef?.dataType === "float" || colDef?.dataType === "integer"
                  ? "number"
                  : "text",
              filterOp:
                colDef?.dataType === "json" || colDef?.dataType === "array"
                  ? "contains"
                  : "equals",
              filterValue:
                typeof value === "string" ? value : JSON.stringify(value),
            }),
          exclude: (colId, value) =>
            addColumnFilter(params.api, {
              columnId: colId,
              filterType: "text",
              filterOp: "not_equals",
              filterValue: String(value),
            }),
          showAllIn: (colId, value) =>
            addColumnFilter(params.api, {
              columnId: colId,
              filterType: "text",
              filterOp: "equals",
              filterValue: value,
            }),
          openRow: (id) => openRowFromGrid(id),
          copyValue: (text) => copyGridText(text, "Value copied"),
          copyRowLink: (id) =>
            copyGridText(
              `${window.location.origin}/dashboard/develop/${dataset}?tab=data&row=${id}`,
              "Row link copied",
            ),
          addToQueue: (id) => {
            const { x, y } = lastPointer.current;
            setQueueTarget({
              rowIds: [id],
              anchorEl: {
                nodeType: 1,
                getBoundingClientRect: () => new DOMRect(x, y, 0, 0),
              },
            });
          },
          toggleSplit: async (id, splitId, on) => {
            try {
              await axios.post(
                `${endpoints.develop.splits.list(dataset)}membership/`,
                {
                  row_ids: [id],
                  add: on ? [splitId] : [],
                  remove: on ? [] : [splitId],
                },
              );
              await invalidateDatasetSplits(queryClient, dataset);
              params.api.refreshServerSide({ purge: false });
              enqueueSnackbar("Split membership saved", { variant: "success" });
            } catch {
              enqueueSnackbar("Couldn't save split membership", {
                variant: "error",
              });
            }
          },
          manageSplits: () => setManageSplitsOpen(true),
          commentOnRow: (id) =>
            setSearchParams((p) => commentDeepLink(p, id), { replace: false }),
          translateCell: (id, colId) =>
            setSearchParams((p) => translateCellDeepLink(p, id, colId)),
          assignRow: (id) => setAssignRowId(id),
        },
        collab: {
          canAssign,
          assignReason: roleViewer
            ? "Viewers can't assign rows"
            : accessReason("edit"),
        },
      });
    },
    [
      versioned,
      queryClient,
      splitData,
      dataset,
      openRowFromGrid,
      setManageSplitsOpen,
      setSearchParams,
      canAssign,
      roleViewer,
      accessReason,
    ],
  );

  if (readFailure?.datasetId === dataset) {
    return (
      <DatasetReadError
        error={readFailure.error}
        onRetry={() => setReadFailure(null)}
      />
    );
  }

  return (
    <AudioPlaybackProvider>
      <DataTabLayout>
        <Box
          className={`ag-theme-quartz dataset-table${
            versioned && _viewOptions.showCheckbox
              ? ` ${hoverSelectClass(hasSelection)}`
              : ""
          }`}
          id="fa-data-grid-shell"
          onMouseDownCapture={() => {
            pointerDownAt.current = Date.now();
          }}
          sx={{
            position: "relative",
            flex: 1,
            padding: "12px",
            paddingTop: "8px",
            backgroundColor: "background.paper",
            // Filter chips and pills stack above the grid; the grid takes
            // the rest instead of overflowing the page.
            ...(versioned && {
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              minWidth: 0,
              // One view (§3.2): a header's ⋮ shows on header hover or
              // focus (or while its menu is open); right-click and
              // Shift+F10 open the same menu.
              "& .ag-header-cell [aria-label$=' column menu']": {
                opacity: 0,
                transition: "opacity 120ms ease",
              },
              "& .ag-header-cell:hover [aria-label$=' column menu'], & .ag-header-cell:focus-within [aria-label$=' column menu'], & .ag-header-cell.ag-header-active [aria-label$=' column menu']":
                { opacity: 1 },
              "@media (hover: none)": {
                "& .ag-header-cell [aria-label$=' column menu']": {
                  opacity: 1,
                },
              },
            }),
          }}
        >
          <DevelopFilterBox />
          {versioned && <SystemFilterPills datasetId={dataset} />}
          {versioned && (
            <SameOnEveryRowLine datasetId={dataset} columns={columnConfig} />
          )}
          <MultiImageViewerProvider>
            <SingleImageViewerProvider>
              <CompositeEvalDialog />
              {isData === true ? (
                <>
                  <TopBanner />
                  <AgGridReact
                    containerStyle={
                      versioned ? VERSIONED_GRID_CONTAINER : undefined
                    }
                    rowHeight={rowHeight}
                    rowSelection={
                      _viewOptions.showCheckbox
                        ? {
                            mode: "multiRow",
                          }
                        : undefined
                    }
                    selectionColumnDef={selectionColumnDef}
                    ref={gridApiRef}
                    components={{
                      JsonCellEditor: JsonCellEditor,
                    }}
                    theme={agTheme}
                    columnDefs={columnDefs}
                    defaultColDef={defaultColDef}
                    pagination={false}
                    cacheBlockSize={DATASET_ROWS_LIMIT}
                    rowBuffer={10}
                    maxBlocksInCache={50}
                    suppressServerSideFullWidthLoadingRow={true}
                    serverSideInitialRowCount={DATASET_ROWS_LIMIT}
                    statusBar={statusBar}
                    rowClassRules={versionRowClassRules}
                    tooltipShowDelay={300}
                    rowModelType="serverSide"
                    onGridReady={(params) => {
                      setGridApi(params.api);
                      gridView.setReady();
                      onGridReady(params);
                      if (versioned) shaping.onGridReady(params);
                    }}
                    {...(versioned && {
                      // The column menu (actions + Sort/Freeze/Move/Hide/
                      // Columns…) is taller than a short grid: let AG's
                      // popups use the page, not the grid box.
                      popupParent: document.body,
                      maintainColumnOrder: shaping.maintainColumnOrder,
                      onNewColumnsLoaded: (params) => {
                        shaping.onNewColumnsLoaded(params);
                        gridView.setReady();
                      },
                      onColumnResized: shaping.onColumnResized,
                    })}
                    onSortChanged={({ api }) => {
                      gridView.setSort(
                        api
                          .getColumnState()
                          .filter((column) => column.sort)
                          .sort(
                            (a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0),
                          )
                          .map(({ colId, sort }) => ({ colId, sort })),
                      );
                    }}
                    onCellValueChanged={onCellValueChanged}
                    getContextMenuItems={
                      versioned ? contextMenuItems : undefined
                    }
                    onCellContextMenu={(e) => {
                      lastPointer.current = {
                        x: e.event?.clientX ?? 0,
                        y: e.event?.clientY ?? 0,
                      };
                    }}
                    onCellKeyDown={(e) => {
                      if (!versioned || e.event?.key !== "Enter") return;
                      const id = e.data?.row_id;
                      if (id && !e.data?.__pending) openRowFromGrid(id);
                    }}
                    onCellFocused={(e) => {
                      // While a row is open, ↑/↓ in the grid move it (150 ms).
                      if (!versioned || !openRowId || e.rowIndex == null)
                        return;
                      // Mouse clicks open rows themselves (220 ms timer).
                      if (Date.now() - pointerDownAt.current < 400) return;
                      clearTimeout(focusFollowTimer.current);
                      focusFollowTimer.current = setTimeout(() => {
                        const node = e.api.getDisplayedRowAtIndex(e.rowIndex);
                        const id = node?.data?.row_id;
                        if (id && id !== openRowId && !node.data.__pending)
                          openRowFromGrid(id, { push: false });
                      }, 150);
                    }}
                    onColumnMoved={
                      versioned ? shaping.onColumnMoved : onColumnChanged
                    }
                    onColumnPinned={
                      versioned ? shaping.onColumnPinned : onColumnChanged
                    }
                    onColumnVisible={
                      versioned ? shaping.onColumnVisible : onColumnChanged
                    }
                    dataTypeDefinitions={dataTypeDefinitions}
                    postProcessPopup={postProcessDatasetPopup}
                    isApplyServerSideTransaction={() => true}
                    stopEditingWhenCellsLoseFocus
                    onCellDoubleClicked={(e) => {
                      clearTimeout(rowOpenTimer.current);
                      doubleClickCellEdit(e);
                    }}
                    getRowId={({ data }) => {
                      return getRowId(data);
                    }}
                    onCellClicked={(params) => {
                      if (!_viewOptions.showDrawer) {
                        return;
                      }
                      if (window.__audioClick) {
                        window.__audioClick = false;
                        return;
                      }
                      if (window.__compositeEvalClick) {
                        window.__compositeEvalClick = false;
                        return;
                      }

                      if (window.__jsonViewerClick) {
                        window.__jsonViewerClick = false;
                        return;
                      }
                      if (window.__imageClick || window.__fileClick) {
                        window.__imageClick = false;
                        window.__fileClick = false;
                        return;
                      }
                      const target = params.event?.target;
                      if (
                        target?.closest(".audio-control-btn") ||
                        target?.closest(".wrapper") ||
                        target?.closest(".render-meta")
                      ) {
                        return;
                      }
                      if (params?.eventPath?.[0]?.localName === "input") {
                        return;
                      }
                      if (
                        params?.column?.getColId() ===
                        APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                      ) {
                        const selected = params.node.isSelected();
                        params.node.setSelected(!selected);
                        return;
                      }
                      if (versioned) {
                        // G9: open after 220 ms; a double click cancels it.
                        // Buttons inside cells (restore, source link, ⤢)
                        // act on their own.
                        if (params.event?.target?.closest?.("button")) return;
                        clearTimeout(rowOpenTimer.current);
                        const id = params.data?.row_id;
                        // A second click on the same cell within 300 ms is a
                        // double click (edit), not an open.
                        const now = Date.now();
                        const cellKey = `${id}:${params?.column?.getColId()}`;
                        if (
                          lastCellClick.current.key === cellKey &&
                          now - lastCellClick.current.at < 300
                        ) {
                          lastCellClick.current = { key: null, at: 0 };
                          return;
                        }
                        lastCellClick.current = { key: cellKey, at: now };
                        rowOpenTimer.current = setTimeout(() => {
                          // Arrow-key focus may already have opened it.
                          if (id && id !== openRowRef.current)
                            openRowFromGrid(id);
                        }, 220);
                        return;
                      }
                      debounceCellClick(
                        () => {
                          if (params.node.rowPinned !== "bottom") {
                            openRow(getRowId(params.data));
                          }
                        },
                        params,
                        220,
                      );
                    }}
                    getRowClass={(params) => {
                      if (!versioned) {
                        return openRowId && getRowId(params.data) === openRowId
                          ? "active-row"
                          : "";
                      }
                      const rowIdValue = params.data?.row_id;
                      const classes = [];
                      if (rowIdValue && rowIdValue === openRowId)
                        classes.push("active-row");
                      if (params.data?.__pending === "new")
                        classes.push("row-pending-new");
                      const deleted = useChangeSetStore
                        .getState()
                        .getOps(dataset)
                        .some(
                          (o) =>
                            o.op === "delete_rows" &&
                            (o.row_ids ?? []).includes(rowIdValue),
                        );
                      if (deleted) classes.push("row-pending-delete");
                      return classes.join(" ");
                    }}
                    className={`develop-data-grid ${!versioned && _viewOptions.bottomRow ? "show-bottom-row" : ""} ${versioned ? `versioned-grid density-${rowHeightFor(gridView.density) == null ? "wrap" : gridView.density}` : ""}`}
                    suppressColumnMoveAnimation={true}
                    suppressAnimationFrame={true}
                    pinnedBottomRowData={
                      !versioned && _viewOptions.bottomRow ? bottomRow : []
                    }
                    loadingOverlayComponent={CustomRowOverlay}
                    loadingOverlayComponentParams={{
                      failedToGenerateData,
                      setOpenSummaryDrawer,
                      gridApiRef,
                      updateProcessingSyntheticData,
                    }}
                    blockLoadDebounceMillis={300}
                  />
                </>
              ) : (
                <AddRowData dataset={dataset} />
              )}
            </SingleImageViewerProvider>
          </MultiImageViewerProvider>
        </Box>
      </DataTabLayout>
      <Suspense fallback={null}>
        <EditColumnName />
        <EditColumnType />
        <ConfirmDeleteColumn dataset={dataset} />
        <AddEvaluationFeeback />
        <ImprovePrompt />
        <DoubleClickEditCell dataset={dataset} />
        <PdfPreviewDrawer />
      </Suspense>

      {versioned && (
        <>
          <ManageSplitsDialog datasetId={dataset} />
          {assignRowId && (
            <AssignDialog
              open
              onClose={() => setAssignRowId(null)}
              datasetId={dataset}
              rowIds={[assignRowId]}
              onAssigned={() => setAssignRowId(null)}
            />
          )}
          {queueTarget && (
            <AddToQueueDialog
              anchorEl={queueTarget.anchorEl}
              onClose={() => setQueueTarget(null)}
              sourceType="dataset_row"
              sourceIds={queueTarget.rowIds}
              itemName="Row"
            />
          )}
          {sourceTrace && (
            <Suspense fallback={null}>
              <TraceDetailDrawerV2
                open
                onClose={closeSourceTrace}
                traceId={sourceTrace.traceId}
                projectId={sourceTrace.projectId}
                initialSpanId={sourceTrace.spanId}
              />
            </Suspense>
          )}
        </>
      )}
    </AudioPlaybackProvider>
  );
};

DevelopDataV2.propTypes = {
  datasetId: PropTypes.string,
  versioned: PropTypes.bool,
  viewOptions: PropTypes.shape({
    showCheckbox: PropTypes.bool,
    showDrawer: PropTypes.bool,
    bottomRow: PropTypes.bool,
  }),
};

export default DevelopDataV2;
