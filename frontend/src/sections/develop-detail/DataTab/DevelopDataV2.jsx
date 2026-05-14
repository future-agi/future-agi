import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  lazy,
  Suspense,
} from "react";
import { AgGridReact } from "ag-grid-react";
import {
  useDatapointDrawerStore,
  useDatapointDrawerStoreShallow,
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
import {
  DATASET_TYPES,
  DUMMY_ROWS,
  enhanceCol,
  getColumnConfig,
  getDatasetViewOptions,
  getTypeDefinitions,
  onCellValueChangedWrapper,
  postProcessPopup,
} from "./common";
import JsonCellEditor from "./DoubleClickEditCell/JsonCellEditor";
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
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { useEvalsList } from "src/sections/common/EvaluationDrawer/getEvalsList";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import SingleImageViewerProvider from "../Common/SingleImageViewer/SingleImageViewerProvider";
import { MultiImageViewerProvider } from "../Common/MultiImageViewer";
import DevelopFilterBox from "./DevelopFilters/DevelopFilterBox";
import TopBanner from "./TopBanner";
import { transformFilter, validateFilter } from "./DevelopFilters/common";
import DatapointDrawerV2 from "./DatapointDrawerV2/DatapointDrawerV2";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";
import AddRowData from "./AddRowData";
import DatasetLoader from "../../develop/loaders/DatasetLoader";
import { useEditSyntheticDataStore } from "src/sections/develop/AddRowDrawer/EditSyntheticData/state";
import RunningSkeletonRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/RunningSkeletonRenderer";
import { APP_CONSTANTS } from "src/utils/constants";
import { OutputTypes } from "../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
const PdfPreviewDrawer = lazy(() => import("src/components/PdfPreviewDrawer"));
const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
];
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
  pinned: true,
  lockPinned: true,
  headerComponent: SelectionHeader,
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
    if (failedToGenerateData) return "failed-to-generate-synthetic";
    if (tableData?.data?.result?.syntheticRegenerate)
      return "regenerating-synthetic";
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
        isSyntheticDataset: Boolean(tableData?.data?.result?.syntheticDataset),
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

const getDataSource = (
  queryClient,
  datasetId,
  setFailedToGenerateData,
  updateRefreshing,
  updateProcessingSyntheticData,
  overlayTimeoutRef,
) => {
  return {
    getRows: async (params) => {
      const { request } = params;
      const pageNumber = Math.floor(request.startRow / DATASET_ROWS_LIMIT);
      const sort = request?.sortModel?.map(({ colId, sort }) => ({
        columnId: colId,
        type: sort === "asc" ? "ascending" : "descending",
      }));
      const filters = useDevelopFilterStore.getState().filters;
      const search = useDevelopSearchStore.getState().search;
      const validFilters = filters.filter(validateFilter).map(transformFilter);
      let tempOrigin;

      if (overlayTimeoutRef.current) {
        clearTimeout(overlayTimeoutRef.current);
        overlayTimeoutRef.current = null;
      }

      try {
        const queryOptions = getDatasetQueryOptions(
          datasetId,
          pageNumber,
          validFilters,
          sort,
          search,
          { enabled: true, staleTime: 0, pageSize: DATASET_ROWS_LIMIT },
        );
        const data = await queryClient.fetchQuery({ ...queryOptions });
        const processingData = data?.data?.result?.isProcessingData;

        useProcessingStore.getState().setIsProcessingData(processingData);

        const rows = processingData
          ? DUMMY_ROWS
          : data?.data?.result?.table ?? [];

        const totalRows = processingData
          ? DUMMY_ROWS.length
          : data?.data?.result?.metadata?.total_rows ?? 0;

        tempOrigin = data?.data?.result?.syntheticDataset
          ? DATASET_TYPES["SYNTHETIC_DATASET"]
          : null;
        const columnConfig = data?.data?.result?.columnConfig;

        if (data?.data?.result?.syntheticDataset) {
          params.api.syntheticDatasetPercentage =
            data?.data?.result?.syntheticDatasetPercentage;
          updateProcessingSyntheticData(
            data?.data?.result?.syntheticDatasetPercentage !== 100,
          );
        }
        updateRefreshing(
          columnConfig?.some((v) => RefreshStatus.includes(v?.status)) ||
            processingData,
        );

        params.api.setGridOption("context", {
          totalRowCount: totalRows ?? 0,
        });

        if (
          data?.data?.result?.syntheticDataset &&
          data?.data?.result?.syntheticDatasetPercentage !== 100
        ) {
          if (overlayTimeoutRef.current) {
            clearTimeout(overlayTimeoutRef.current);
            overlayTimeoutRef.current = null;
          }

          overlayTimeoutRef.current = setTimeout(() => {
            params.api.showLoadingOverlay();
          }, 100);
        } else {
          if (data?.data?.result?.syntheticDataset) {
            updateProcessingSyntheticData(false);
          }
          // Infinite-scroll: don't expose total upfront
          const fetchedRows = rows || [];
          const isLastPage = fetchedRows.length < DATASET_ROWS_LIMIT;
          const lastRow = isLastPage
            ? request.startRow + fetchedRows.length
            : -1;

          params.success({
            rowData: fetchedRows,
            rowCount: lastRow,
          });

          // Prefetch next page so scroll feels instant
          if (!isLastPage) {
            const nextPageOptions = getDatasetQueryOptions(
              datasetId,
              pageNumber + 1,
              validFilters,
              sort,
              search,
              { enabled: true, staleTime: 0, pageSize: DATASET_ROWS_LIMIT },
            );
            queryClient.prefetchQuery({ ...nextPageOptions });
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
        if (isCancelled) return;
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

const getAverageColumnConfig = (columns, tableRows) => {
  if (!columns?.length) {
    return [];
  }
  const grouping = {};

  const bottomRow = {};

  const firstRow = tableRows?.[0];

  for (const eachCol of columns) {
    if (
      eachCol?.sourceId &&
      (eachCol?.originType === "evaluation" ||
        eachCol?.originType === "evaluation_reason")
    ) {
      if (!grouping[eachCol?.sourceId]) {
        grouping[eachCol?.sourceId] = [eachCol];
      } else {
        grouping[eachCol?.sourceId].push(eachCol);
      }
    } else {
      grouping[eachCol?.id] = [eachCol];
    }
  }

  for (const [_, cols] of Object.entries(grouping)) {
    // Ensure evaluation columns come before evaluation_reason so we
    // pick the result column (which carries averageScore) as cols[0].
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
        firstRow?.[eachCol.id]?.valueInfos?.output === OutputTypes.NUMERIC;

      bottomRow[eachCol.id] =
        eachCol?.averageScore === 0 || eachCol?.averageScore ? (
          isNumericOutput ? (
            `Average : ${eachCol?.averageScore}`
          ) : (
            `Average : ${eachCol?.averageScore}%`
          )
        ) : eachCol?.metadata?.averageCost ||
          eachCol?.metadata?.averageLatency ||
          eachCol?.metadata?.averageTokens ? (
          <div
            style={{
              display: "flex",
              gap: 2,
              lineHeight: "1.5",
              alignItems: "center",
            }}
          >
            <Iconify
              icon="material-symbols:schedule-outline"
              sx={{
                display: "flex",
                gap: 0.5,
                alignItems: "center",
                width: 12,
                height: 12,
                marginRight: "2px",
              }}
            />
            {Math.round(eachCol?.metadata?.averageLatency)}ms
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
            {Math.round(eachCol?.metadata?.averageTokens)}
            <Iconify
              icon="material-symbols:attach-money"
              sx={{
                display: "flex",
                alignItems: "center",
                width: 12,
                height: 12,
                marginLeft: "16px",
                marginRight: "-2px",
              }}
            />
            {Number(eachCol?.metadata?.averageCost).toFixed(6)}
          </div>
        ) : (
          ""
        );
    } else {
      const eachCol = cols[0];

      bottomRow[eachCol.id] =
        eachCol?.averageScore === 0 || eachCol?.averageScore
          ? firstRow?.[eachCol.id]?.valueInfos?.output === "numeric"
            ? `Average : ${eachCol?.averageScore}`
            : `Average : ${eachCol?.averageScore}%`
          : "";
    }
  }

  return [
    {
      checkbox: "",
      ...bottomRow,
    },
  ];
};
const DATASET_ROWS_LIMIT = 30;
const BATCH_SIZE = 3;

const DevelopDataV2 = ({ datasetId, viewOptions }) => {
  const { role } = useAuthContext();
  const isViewerRole = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const gridApiRef = useRef(null);
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.dataGrid);
  const queryClient = useQueryClient();
  const { dataset: datasetFromParams } = useParams();
  const _viewOptions = getDatasetViewOptions(viewOptions);

  const dataset = datasetId || datasetFromParams;
  const showSummary = useShowSummaryStoreShallow((s) => s.showSummary);

  // Warm the saved-evals list cache for this dataset so per-cell renderers
  // (CustomCellRender) and the datapoint drawer can synchronously look up
  // the eval_type for an eval column. This is the same query key the
  // EvaluationDrawer uses, so the request is deduped.
  useEvalsList(dataset, { eval_type: "user" }, "dataset");
  const isRefreshingColumns = useRef(false);
  const { setGridApi, setRefetchTable } = useDevelopDetailContext();
  const setEditCell = useEditCellStoreShallow((s) => s.setEditCell);
  const performedClicks = useRef(0);
  const clickTimeout = useRef(null);
  const activeDatapoint = useDatapointDrawerStoreShallow((s) => s.datapoint);
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
    }),
    [],
  );
  isProcessingSyntheticData;

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
    );
    gridApiRef.current?.api?.setGridOption("serverSideDatasource", dataSource);
  }, [
    dataset,
    queryClient,
    setFailedToGenerateData,
    updateRefreshing,
    updateProcessingSyntheticData,
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
      );
      params.api.setGridOption("serverSideDatasource", dataSource);
      params.api.setGridOption("onSelectionChanged", onRowSelectionChanged);
      params.api.setGridOption("onColumnHeaderClicked", onHeaderClicked);
    },
    [
      dataset,
      queryClient,
      setFailedToGenerateData,
      updateRefreshing,
      updateProcessingSyntheticData,
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
          columnConfigOnly: true,
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

  const isSyntheticDataset = Boolean(tableData?.data?.result?.syntheticDataset);

  const statusBar = useMemo(() => {
    return isSyntheticDataset && !processingComplete
      ? undefined
      : {
          statusPanels: [
            {
              statusPanel: DataTabStatusBar,
              align: "left",
              key: "rowCounter",
            },
          ],
        };
  }, [isSyntheticDataset, processingComplete]);

  const columnConfig = useMemo(() => {
    if (averageMetaData?.isProcessingData) {
      return averageMetaData?.columnConfig ?? [];
    }
    const tc = tableData?.data?.result?.columnConfig;
    return tc?.length ? tc : averageMetaData?.columnConfig ?? [];
  }, [
    tableData?.data?.result?.columnConfig,
    averageMetaData?.columnConfig,
    averageMetaData?.isProcessingData,
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

  const onCellValueChanged = onCellValueChangedWrapper(queryClient, dataset);

  const columnDefs = useMemo(() => {
    if (isLoadingTable || averageMetaData?.isProcessingData) {
      return getDefaultColDefs();
    }
    const grouping = {};
    for (const eachCol of columnConfig) {
      if (
        eachCol?.sourceId &&
        (eachCol?.originType === "evaluation" ||
          eachCol?.originType === "evaluation_reason")
      ) {
        if (!grouping[eachCol?.sourceId]) {
          grouping[eachCol?.sourceId] = [eachCol];
        } else {
          grouping[eachCol?.sourceId].push(eachCol);
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
          }),
        );
      } else {
        let eachCol = enhanceCol(cols[0], averageMetaData?.columnConfig);
        let children = null;
        // showSummary is keyed by sourceId (the stable eval binding id),
        // not by the ag-grid column id — the column id would otherwise flip
        // between the evaluation / evaluation_reason child column ids once
        // the grouping is applied.
        const groupKey = eachCol?.sourceId || eachCol.id;
        if (showSummary.includes(groupKey)) {
          eachCol = enhanceCol(
            cols.find((v) => v?.originType === "evaluation"),
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
          }),
        );
      }
    }
    return columnMap;
  }, [
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
  ]);

  const isData = useMemo(() => {
    const isSdk = Boolean(tableData?.data?.result?.datasetConfig?.isSdk);
    if (tableData === undefined) return true;
    return Boolean(!isSdk || tableData?.data?.result?.table.length);
  }, [tableData]);

  const refreshRowsManual = useCallback(async () => {
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
          columnId: colId,
          type: sort === "asc" ? "ascending" : "descending",
        });
      }
      return acc;
    }, []);

    const filters = useDevelopFilterStore.getState().filters;
    const search = useDevelopSearchStore.getState().search;
    const validFilters = filters.filter(validateFilter).map(transformFilter);

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
          const rows = data?.data?.result?.isProcessingData
            ? DUMMY_ROWS
            : data?.data?.result?.table;

          const columnConfig = data?.data?.result?.isProcessingData
            ? getDefaultColDefs()
            : data?.data?.result?.columnConfig;

          if (data?.data?.result?.syntheticDataset) {
            const initialState = isProcessingSyntheticData.current;
            isProcessingSyntheticData.current =
              data?.data?.result?.syntheticDatasetPercentage !== 100;

            gridApiRef.current.api.syntheticDatasetPercentage =
              data?.data?.result?.syntheticDatasetPercentage;
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
            data?.data?.result?.syntheticDataset &&
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
  }, [dataset, queryClient, refreshAverage]);

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
      gridApiRef.current?.api?.refreshServerSide();
    }
    wasProcessingData.current = Boolean(averageMetaData?.isProcessingData);
  }, [averageMetaData?.isProcessingData, refetchTableData]);

  const onColumnChanged = useCallback(
    (params) => {
      if (
        (!params.finished && params.type === "columnMoved") ||
        params.source === "gridOptionsChanged"
      ) {
        return;
      }

      const newColumnOrder = params.api
        .getColumnState()
        .filter(
          ({ colId }) => colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN,
        );

      queryClient.setQueryData(
        getDatasetQueryKey(dataset, 0, [], [], ""),
        (oldData) => {
          const existingColumnOrder = oldData?.data?.result?.columnConfig;
          const colDefMap = existingColumnOrder.reduce((acc, col) => {
            acc[col.id] = col;
            return acc;
          }, {});
          const newState = newColumnOrder.map((state) => {
            const pinned =
              params.type === "columnPinned" &&
              params.column?.colId === state.colId
                ? params?.pinned
                : colDefMap[state.colId]?.isFrozen;

            const visible =
              params.type === "columnVisible" &&
              params.column?.colId === state.colId
                ? params.visible
                : colDefMap[state.colId]?.isVisible;

            return {
              ...colDefMap[state.colId],
              isFrozen: pinned,
              isVisible: visible,
            };
          });

          return {
            ...oldData,
            data: {
              ...oldData.data,
              result: { ...oldData.data.result, columnConfig: newState },
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
      const dataType = event?.colDef?.dataType;
      const originType = event?.colDef?.originType;
      const colId = event?.column?.colId;

      if (
        (dataType === "audio" ||
          dataType === "image" ||
          dataType === "persona") &&
        colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN &&
        ![
          "run_prompt",
          "evaluation",
          "optimization",
          "annotation_label",
          "evaluation_reason",
        ].includes(originType)
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
    };
  }, []);
  return (
    <AudioPlaybackProvider>
      <Box
        className="ag-theme-quartz dataset-table"
        sx={{
          flex: 1,
          padding: "12px",
          paddingTop: "8px",
          backgroundColor: "background.paper",
        }}
      >
        <DevelopFilterBox />
        <MultiImageViewerProvider>
          <SingleImageViewerProvider>
            {isData === true ? (
              <>
                <TopBanner />
                <AgGridReact
                  rowHeight={defaultRowHeightMapping[cellHeight]?.height}
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
                  maxBlocksInCache={5}
                  suppressServerSideFullWidthLoadingRow={true}
                  serverSideInitialRowCount={DATASET_ROWS_LIMIT}
                  statusBar={statusBar}
                  rowModelType="serverSide"
                  onGridReady={(params) => {
                    setGridApi(params.api);
                    onGridReady(params);
                  }}
                  onCellValueChanged={onCellValueChanged}
                  onColumnMoved={onColumnChanged}
                  onColumnPinned={onColumnChanged}
                  onColumnVisible={onColumnChanged}
                  dataTypeDefinitions={dataTypeDefinitions}
                  postProcessPopup={postProcessPopup}
                  isApplyServerSideTransaction={() => true}
                  stopEditingWhenCellsLoseFocus
                  onCellDoubleClicked={doubleClickCellEdit}
                  getRowId={({ data }) => {
                    return data.rowId;
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
                    debounceCellClick(() => {
                      if (params.node.rowPinned !== "bottom") {
                        // logger.debug("params", params);
                        const datapointValue = {
                          index: params.rowIndex,
                          rowData: params.data,
                          valueInfos:
                            params?.data[params?.colDef?.col?.id]?.valueInfos,
                        };
                        useDatapointDrawerStore
                          .getState()
                          .setDatapoint(datapointValue);
                        useDatapointDrawerStore
                          .getState()
                          .setDrawerColumn(params?.colDef);
                      }
                    }, 0);
                  }}
                  getRowClass={(params) =>
                    params.node.rowIndex === activeDatapoint?.index
                      ? "active-row"
                      : ""
                  }
                  className={`develop-data-grid ${_viewOptions.bottomRow ? "show-bottom-row" : ""}`}
                  suppressColumnMoveAnimation={true}
                  suppressAnimationFrame={true}
                  pinnedBottomRowData={_viewOptions.bottomRow ? bottomRow : []}
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
      <Suspense fallback={null}>
        <EditColumnName />
        <EditColumnType />
        <ConfirmDeleteColumn dataset={dataset} />
        <AddEvaluationFeeback />
        <ImprovePrompt />
        <DoubleClickEditCell dataset={dataset} />
        <PdfPreviewDrawer />
      </Suspense>

      <DatapointDrawerV2 />
    </AudioPlaybackProvider>
  );
};

DevelopDataV2.propTypes = {
  datasetId: PropTypes.string,
  viewOptions: PropTypes.shape({
    showCheckbox: PropTypes.bool,
    showDrawer: PropTypes.bool,
    bottomRow: PropTypes.bool,
  }),
};

export default DevelopDataV2;
