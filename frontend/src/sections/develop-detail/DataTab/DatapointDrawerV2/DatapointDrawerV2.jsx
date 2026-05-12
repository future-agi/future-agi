import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Drawer,
  IconButton,
  Skeleton,
  Tooltip,
  Typography,
  useTheme,
  Fade,
} from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  useAddEvaluationFeebackStore,
  useDatapointDrawerStore,
  useImprovePromptStore,
} from "../../states";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { enhanceCol, getStatusColor } from "../common";
import { getLabel } from "../common";
import DatapointCard from "src/sections/common/DatapointCard";
import ImageDatapointCard from "src/sections/common/ImageDatapointCard";
import ImagesDatapointCard from "src/sections/common/ImagesDatapointCard";
import AudioDatapointCard from "src/components/custom-audio/AudioDatapointCard";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import AudioErrorCard from "src/components/custom-audio/AudioErrorCard";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { useParams } from "react-router";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import logger from "src/utils/logger";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import DocumentDatapointCard from "../../../common/DocumentDatapointCard";
import StatusCellRenderer from "./StatusCellRenderer";
import LoadingOverlay from "src/components/loading-screen/LoadingOverlayDataPointDataset";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import AnnotationSidebarContent from "src/components/traceDetailDrawer/AnnotationSidebarContent";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import { useEvalsList } from "src/sections/common/EvaluationDrawer/getEvalsList";
import CompositeResultView from "src/sections/evals/components/CompositeResultView";
import { canonicalEntries } from "src/utils/utils";

const SkeletonLoader = () => (
  <Box
    sx={{
      paddingX: 1,
      display: "flex",
      alignItems: "center",
      height: "100%",
    }}
  >
    <Skeleton sx={{ width: "100%", height: "10px" }} variant="rounded" />
  </Box>
);

const hasRenderableCellValue = (value) =>
  value !== undefined && value !== null && value !== "";

const ViewDetailsCellRenderer = (props) => {
  const { data = {}, node, setRunEval, disabled } = props;
  const theme = useTheme();

  const handleClick = () => {
    const metadata = data?.data?.column?.col?.metadata;
    if (!disabled) {
      setRunEval({
        ...node?.data?.data?.description,
        evalName: node?.data?.data?.eval_name,
        metadata: metadata,
        evalMetricId: data?.data?.column?.col?.sourceId,
      });
    }
  };

  return (
    <Box
      component="span"
      onClick={handleClick}
      sx={{
        color: disabled
          ? theme.palette.text.disabled
          : theme.palette.primary.main,
        cursor: disabled ? "not-allowed" : "pointer",
        pointerEvents: disabled ? "none" : "auto",
        fontFamily: "inherit",
        fontSize: "13px",
        fontWeight: 500,
        textDecoration: "none",
        "&:hover": { textDecoration: disabled ? "none" : "underline" },
      }}
    >
      View Detail
    </Box>
  );
};

ViewDetailsCellRenderer.propTypes = {
  setEvalDrawer: PropTypes.func,
  setRunEval: PropTypes.func,
  node: PropTypes.shape({
    data: PropTypes.object,
  }),
  colDef: PropTypes.object,
  disabled: PropTypes.bool,
  data: PropTypes.object,
};

const STATUS = {
  RUNNING: "running",
  ERROR: "error",
};

const DATAPOINT_DRAWER_THEME_PARAMS = {
  headerColumnBorder: { width: "0px" },
  headerBackgroundColor: "whiteSpace.50",
  headerFontFamily: "IBM Plex Sans",
  headerFontWeight: "fontWeightMedium",
  headerFontSize: "14px",
};

const NavIconButton = ({ icon, tooltip, onClick, disabled, loading }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span>
      <Box
        component="button"
        type="button"
        onClick={onClick}
        disabled={disabled}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 24,
          height: 24,
          p: 0,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "2px",
          bgcolor: "background.paper",
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.4 : 1,
          flexShrink: 0,
          "&:hover:not(:disabled)": {
            bgcolor: "action.hover",
            borderColor: "text.disabled",
          },
          transition: "all 120ms",
        }}
      >
        {loading ? (
          <CircularProgress size={12} sx={{ color: "text.secondary" }} />
        ) : (
          <Iconify icon={icon} width={20} sx={{ color: "text.primary" }} />
        )}
      </Box>
    </span>
  </Tooltip>
);

NavIconButton.propTypes = {
  icon: PropTypes.string.isRequired,
  tooltip: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  loading: PropTypes.bool,
};

const DatapointDrawerChild = () => {
  const { dataset } = useParams();
  const theme = useTheme();
  const agTheme = useAgThemeWith(DATAPOINT_DRAWER_THEME_PARAMS);
  const queryClient = useQueryClient();
  const { gridApi } = useDevelopDetailContext();
  const allColumns = useDatasetColumnConfig(dataset);
  const { datapoint, setDatapoint, setDrawerColumn, column } =
    useDatapointDrawerStore();
  const columnRefToScroll = useRef({});
  const [showContent, setShowContent] = useState(false);
  const [annotateOpen, setAnnotateOpen] = useState(false);
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);
  const isNavigatingRef = useRef(false);

  useEffect(() => {
    if (datapoint) {
      if (isNavigatingRef.current) {
        setShowContent(true);
        isNavigatingRef.current = false;
      } else {
        setShowContent(false);
        const timer = setTimeout(() => {
          setShowContent(true);
        }, 1000);

        return () => clearTimeout(timer);
      }
    } else {
      setShowContent(false);
    }
  }, [datapoint]);

  const onClose = () => {
    setDatapoint(null);
    setDrawerColumn(null);
  };
  const { setAddEvaluationFeeback } = useAddEvaluationFeebackStore();
  const { setImprovePrompt } = useImprovePromptStore();

  const totalRowCount =
    gridApi.current?.getGridOption("context")?.totalRowCount;

  const { mutateAsync: getNextItemIds, isPending: isLoadingNextItemIds } =
    useMutation({
      mutationFn: (d) => {
        return axios.post(endpoints.develop.getRowData(dataset), d);
      },
    });

  const { mutateAsync: getCellData, isPending: isLoadingCellData } =
    useMutation({
      mutationFn: (d) => {
        return axios.post(endpoints.develop.getCellData, d);
      },
    });

  const [rows, setRows] = useState(() => {
    const newRows = [];

    gridApi.current?.forEachNode((node) => {
      if (node.displayed && node.id) {
        newRows.push({ rowData: node.data, id: node.id });
      }
    });

    return newRows;
  });

  const mainData = useMemo(() => {
    const evalColumnFields = allColumns
      .filter((col) => col.originType !== "evaluation")
      .map((col) => col.field);

    const filteredRowData = Object.fromEntries(
      Object.entries(datapoint?.rowData ? datapoint?.rowData : {}).filter(
        ([key]) => evalColumnFields.includes(key),
      ),
    );

    return filteredRowData;
  }, [allColumns, datapoint?.rowData]);

  const { data: averageMetaData } = useQuery({
    queryKey: ["dataset-detail-average", dataset],
    select: (d) => d.data?.result?.columnConfig,
    enabled: false,
  });

  // Fetch the saved-evals list to build a sourceId → eval_type lookup.
  // This shares the cache with EvaluationDrawer so there's no extra
  // network hop when both are open. Used to gate cell-level features
  // (error localization, feedback) that don't apply to code evals.
  const { data: savedEvalsData } = useEvalsList(
    dataset,
    { eval_type: "user" },
    "dataset",
  );
  const evalTypeBySourceId = useMemo(() => {
    const map = {};
    (savedEvalsData?.evals || []).forEach((e) => {
      const key = e.id || e.user_eval_id;
      if (key) map[key] = e.eval_type || e.evalType;
    });
    return map;
  }, [savedEvalsData]);
  const isCodeEvalColumn = (col) => {
    const sourceId = col?.sourceId || col?.source_id;
    return evalTypeBySourceId[sourceId] === "code";
  };
  const evalOpenIsCode = isCodeEvalColumn(column?.col);

  const runEvalData = useMemo(() => {
    const evalColumns = allColumns.filter((i) => i.originType === "evaluation");
    const currentRowData = datapoint?.rowData ? datapoint?.rowData : [];

    const valueInfos = evalColumns.map((column) => {
      const columnId = column.field;
      const rowDataForColumn = currentRowData?.[columnId];

      // The axios snake→camel response interceptor was removed
      // (2026-04-12) so backend payloads land as snake_case in JS.
      // Read both shapes for compatibility with any cached/stale data
      // and re-emit as snake_case for downstream consumers.
      const cellValue =
        rowDataForColumn?.cell_value ?? rowDataForColumn?.cellValue ?? null;
      const valueInfosOutput =
        rowDataForColumn?.value_infos?.output ??
        rowDataForColumn?.valueInfos?.output;
      const baseData = {
        data: {
          column: {
            ...column,
            col: enhanceCol(column.col, averageMetaData),
            output: valueInfosOutput,
          },
          eval_name: column.headerName,
          description: rowDataForColumn ?? null,
          cell_value: cellValue,
        },
      };

      if (!rowDataForColumn) {
        return { ...baseData, isLoading: true };
      }

      if (rowDataForColumn.status === STATUS.RUNNING) {
        return { ...rowDataForColumn, ...baseData, isLoading: true };
      }

      if (rowDataForColumn.status === STATUS.ERROR) {
        return { ...rowDataForColumn, ...baseData, isError: true };
      }

      return baseData;
    });

    const flattenedValueInfos = valueInfos.flat();

    return flattenedValueInfos;
  }, [datapoint, allColumns, averageMetaData]);

  const [evalOpen, setEvalOpen] = useState(() => {
    if (column?.originType === "evaluation" && datapoint?.rowData) {
      const currentField = datapoint?.rowData[column?.field];
      const isRunning = currentField?.status === "running";

      if (isRunning || !currentField) {
        return null;
      }

      const enhancedColumn = enhanceCol(column.col, averageMetaData);

      return {
        ...currentField,
        metadata: enhancedColumn?.metadata,
        evalMetricId: column?.col?.sourceId,
      };
    } else {
      return null;
    }
  });
  const evalOutput = evalOpen?.valueInfos?.output;

  const loading = false;

  const audioColumnIds = allColumns
    .filter((col) => col.dataType === "audio")
    .map((col) => col.id || col.field);

  const imageColumnIds = allColumns
    .filter((col) => col.dataType === "image")
    .map((col) => col.id || col.field);

  const imagesColumnIds = allColumns
    .filter((col) => col.dataType === "images")
    .map((col) => col.id || col.field);

  const documentColumnIds = allColumns
    .filter((col) => col.dataType === "document")
    .map((col) => col.id || col.field);

  const TabColumnDefs = [
    {
      headerName: "Evaluation Metrics",
      field: "data.eval_name",
      flex: 1,
      cellRenderer: (params) => (loading ? <SkeletonLoader /> : params.value),
    },
    {
      headerName: "Score",
      field: "data.cell_value",
      flex: 1,
      cellRenderer: (params) =>
        loading ? (
          <SkeletonLoader />
        ) : (
          <StatusCellRenderer
            cellValue={params.data?.data?.cell_value}
            status={params.data?.status}
            isLoading={params.data?.isLoading}
            type={params.data?.data?.column?.output}
          />
        ),
    },
    {
      headerName: "Description",
      field: "description",
      flex: 1,
      cellRenderer: (params) =>
        loading ? (
          <SkeletonLoader />
        ) : (
          <ViewDetailsCellRenderer
            {...params}
            setRunEval={setEvalOpen}
            disabled={params.data?.isLoading}
          />
        ),
    },
  ];

  useEffect(() => {
    if (!column) return;

    const scrollTimer = setTimeout(() => {
      const el = columnRefToScroll.current[column?.field];
      if (el) {
        el.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }
    }, 1000);

    return () => clearTimeout(scrollTimer);
  }, [column]);

  const defaultColDef = {
    lockVisible: true,
    sortable: true,
    filter: false,
    resizable: true,
    suppressHeaderMenuButton: true,
    suppressHeaderContextMenu: true,
  };

  const finalArray = useMemo(() => {
    if (
      evalOpen?.cellValue &&
      evalOpen?.cellValue.startsWith("[") &&
      evalOpen?.cellValue.endsWith("]")
    ) {
      return JSON.parse(evalOpen?.cellValue.replace(/'/g, '"'));
    }
  }, [evalOpen?.cellValue]);

  const onNavigate = async (direction) => {
    isNavigatingRef.current = true;

    if (direction === "next") {
      const nextIndex = datapoint.index + 1;
      if (rows?.[nextIndex] && rows[nextIndex]?.rowData) {
        const rowData = rows[nextIndex]?.rowData;
        setDatapoint({
          index: nextIndex,
          rowData: rowData,
          valueInfos: rows[nextIndex]?.rowData?.valueInfos,
        });
        if (evalOpen) {
          const column = allColumns.find(
            (i) => i?.col?.sourceId === evalOpen?.evalMetricId,
          );

          setEvalOpen({
            ...evalOpen,
            ...rowData[column?.field],
          });
        }
      } else if (rows?.[nextIndex] && !rows[nextIndex]?.rowData) {
        const nextId = rows[nextIndex]?.id;
        try {
          const newCellData = await getCellData({
            row_ids: [nextId],
            column_ids: allColumns.map((i) => i?.col?.id),
          });

          const nextCellData = newCellData?.data?.result?.[nextId];

          if (nextCellData) {
            setDatapoint({
              index: nextIndex,
              rowData: nextCellData,
              valueInfos: nextCellData?.valueInfos,
            });
            setRows((prev) => {
              const newRows = [...prev];
              newRows[nextIndex] = {
                rowData: nextCellData,
                id: nextId,
              };
              return newRows;
            });
            if (evalOpen) {
              const column = allColumns.find(
                (i) => i?.col?.sourceId === evalOpen?.evalMetricId,
              );
              setEvalOpen({
                ...evalOpen,
                ...nextCellData[column?.field],
              });
            }
          }
        } catch (e) {
          logger.error("Failed to get next item ids", { e });
        }
      } else {
        const mergedRows = [...rows];
        try {
          const nextIds = await getNextItemIds({
            row_id: datapoint?.rowData?.rowId,
          });
          const newIds = nextIds?.data?.result?.next?.rowId;
          if (newIds && newIds?.length > 0) {
            newIds.forEach((id) => {
              mergedRows.push({ rowData: null, id: id });
            });
          }
        } catch (e) {
          logger.error("Failed to get next item ids", { e });
        }

        const nextId = mergedRows[nextIndex]?.id;

        try {
          const newCellData = await getCellData({
            row_ids: [nextId],
            column_ids: allColumns.map((i) => i?.col?.id),
          });

          const nextCellData = newCellData?.data?.result?.[nextId];

          if (nextCellData) {
            mergedRows[nextIndex] = {
              rowData: nextCellData,
              id: nextId,
            };
          }
          setDatapoint({
            index: nextIndex,
            rowData: nextCellData,
            valueInfos: nextCellData?.valueInfos,
          });
          if (evalOpen) {
            const column = allColumns.find(
              (i) => i?.col?.sourceId === evalOpen?.evalMetricId,
            );
            setEvalOpen({
              ...evalOpen,
              ...nextCellData[column?.field],
            });
          }
        } catch (e) {
          logger.error("Failed to get previous item ids", { e });
        }

        setRows(mergedRows);
      }
    } else if (direction === "previous") {
      const rowData = rows[datapoint.index - 1].rowData;
      setDatapoint({
        index: datapoint.index - 1,
        rowData,
        valueInfos: rows[datapoint.index - 1]?.rowData?.valueInfos,
      });
      if (evalOpen) {
        const column = allColumns.find(
          (i) => i?.col?.sourceId === evalOpen?.evalMetricId,
        );
        setEvalOpen({
          ...evalOpen,
          ...rowData[column?.field],
        });
      }
    }
  };

  const navStateRef = useRef({});
  navStateRef.current = {
    onNavigate,
    datapointIndex: datapoint?.index,
    totalRowCount,
    navLoading: isLoadingNextItemIds || isLoadingCellData,
    enabled: Boolean(datapoint),
  };

  useEffect(() => {
    const handleKeyDown = (e) => {
      const state = navStateRef.current;
      if (!state.enabled) return;

      const target = e.target;
      if (
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable
      )
        return;

      const isNext = e.key === "j" || e.key === "J";
      const isPrev = e.key === "k" || e.key === "K";
      if (!isNext && !isPrev) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (
        isNext &&
        !state.navLoading &&
        state.datapointIndex !== state.totalRowCount - 1
      ) {
        e.preventDefault();
        e.stopPropagation();
        state.onNavigate("next");
      } else if (isPrev && state.datapointIndex !== 0) {
        e.preventDefault();
        e.stopPropagation();
        state.onNavigate("previous");
      }
    };

    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, []);

  return (
    <Box
      sx={{
        display: "flex",
        height: "100vh",
        justifyContent: "flex-end",
        position: "relative",
      }}
    >
      {!showContent && <LoadingOverlay />}

      <Fade in={showContent} timeout={100}>
        <Box
          sx={{
            display: "flex",
            height: "100vh",
            justifyContent: "flex-end",
            width: "100%",
          }}
        >
          <Collapse
            in={annotateOpen}
            orientation="horizontal"
            sx={{ overflowY: "auto" }}
            unmountOnExit
          >
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                minHeight: "100vh",
                maxHeight: "100vh",
                width: "400px",
                borderRight: "2px solid rgba(147, 143, 163, 0.2)",
              }}
            >
              <AnnotationSidebarContent
                sources={[
                  {
                    sourceType: "dataset_row",
                    sourceId: datapoint?.rowData?.rowId,
                  },
                ]}
                onClose={() => setAnnotateOpen(false)}
                onScoresChanged={() => {}}
                onAddLabel={() => setAddLabelDrawerOpen(true)}
              />
            </Box>
          </Collapse>
          <Collapse
            in={evalOpen}
            orientation="horizontal"
            sx={{ overflowY: "auto" }}
            unmountOnExit
          >
            <Box
              sx={{
                paddingTop: "20px",
                paddingLeft: "5px",
                gap: "20px",
                display: "flex",
                flexDirection: "column",
                minHeight: "100vh",
                maxHeight: "100vh",
                width: "550px",
                position: "relative",
                overflowY: "auto",
                borderRight: "2px solid rgba(147, 143, 163, 0.2)",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  top: 0,
                  position: "sticky",
                  paddingLeft: "20px",
                  paddingRight: "20px",
                }}
              >
                <Typography
                  variant="m3"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  {evalOpen?.eval_name || column?.headerName}
                </Typography>
                <Button
                  variant="soft"
                  size="small"
                  sx={{
                    backgroundColor: "action.hover",
                    color: "text.secondary",
                  }}
                  onClick={() => setEvalOpen(null)}
                >
                  Close
                </Button>
              </Box>
              <Box
                sx={{
                  flex: 1,
                  paddingLeft: "20px",
                  paddingRight: "20px",
                  flexDirection: "column",
                  gap: "15px",
                  height: "100%",
                  overflow: "auto",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "5px",
                    marginBottom: "25px",
                  }}
                >
                  <Typography
                    fontWeight={500}
                    fontSize={14}
                    color="text.primary"
                  >
                    Score
                  </Typography>
                  <Box>
                    {evalOpen?.status === "error" ? (
                      <Box
                        sx={{
                          color: theme.palette.error.main,
                          fontSize: "14px",
                        }}
                      >
                        Error
                      </Box>
                    ) : (
                      hasRenderableCellValue(evalOpen?.cellValue) && (
                        <>
                          <ShowComponent condition={!Array.isArray(finalArray)}>
                            <Chip
                              variant="soft"
                              label={getLabel(evalOpen?.cellValue)}
                              size="small"
                              sx={{
                                ...getStatusColor(evalOpen?.cellValue, theme),
                                transition: "none",
                                "&:hover": {
                                  backgroundColor: getStatusColor(
                                    evalOpen?.cellValue,
                                    theme,
                                  ).backgroundColor,
                                  boxShadow: "none",
                                },
                              }}
                            />
                          </ShowComponent>
                          <ShowComponent condition={Array.isArray(finalArray)}>
                            <ShowComponent condition={finalArray?.length === 0}>
                              <Chip
                                variant="soft"
                                label={"None"}
                                size="small"
                                sx={{
                                  backgroundColor: theme.palette.red.o10,
                                  color: theme.palette.red[500],
                                  marginRight: "10px",
                                  transition: "none",
                                  "&:hover": {
                                    backgroundColor: theme.palette.red[500],
                                    boxShadow: "none",
                                  },
                                }}
                              />
                            </ShowComponent>
                            <ShowComponent condition={finalArray?.length > 0}>
                              {finalArray?.map((val) => (
                                <Chip
                                  key={val}
                                  variant="soft"
                                  label={val}
                                  size="small"
                                  sx={{
                                    ...getStatusColor(
                                      evalOpen?.cellValue,
                                      theme,
                                    ),
                                    marginRight: theme.spacing(1),
                                    transition: "none",
                                    "&:hover": {
                                      backgroundColor: getStatusColor(
                                        evalOpen?.cellValue,
                                        theme,
                                      ).backgroundColor,
                                      boxShadow: "none",
                                    },
                                  }}
                                />
                              ))}
                            </ShowComponent>
                          </ShowComponent>
                        </>
                      )
                    )}
                  </Box>
                </Box>

                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "8px",
                    marginBottom: "25px",
                    overflowWrap: "break-word",
                  }}
                >
                  <Typography
                    fontWeight={500}
                    fontSize={14}
                    color="text.primary"
                  >
                    Explanation
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid var(--border-default)",
                      padding: "16px",
                      borderRadius: "4px",
                    }}
                  >
                    {Array.isArray(evalOpen?.valueInfos?.children) &&
                    evalOpen.valueInfos.children.length > 0 ? (
                      (() => {
                        /** @type {any[]} */
                        const compositeChildren =
                          evalOpen.valueInfos.children || [];
                        return (
                          <CompositeResultView
                            compositeResult={{
                              ...evalOpen.valueInfos,
                              total_children:
                                evalOpen.valueInfos.total_children ??
                                compositeChildren.length,
                              completed_children:
                                evalOpen.valueInfos.completed_children ??
                                compositeChildren.filter(
                                  (child) => child.status === "completed",
                                ).length,
                              failed_children:
                                evalOpen.valueInfos.failed_children ??
                                compositeChildren.filter(
                                  (child) => child.status === "failed",
                                ).length,
                            }}
                          />
                        );
                      })()
                    ) : evalOpen?.valueInfos?.reason?.trim() ||
                      evalOpen?.valueInfos?.summary ? (
                      <CellMarkdown
                        spacing={0}
                        text={
                          evalOpen?.valueInfos?.reason ||
                          evalOpen?.valueInfos?.summary
                        }
                      />
                    ) : (
                      "Unable to fetch Explanation"
                    )}
                  </Box>
                </Box>
                {/* Code evals don't produce model traces for the localizer
                    to introspect — hide the section entirely. */}
                {!evalOpenIsCode && (
                  <ErrorLocalizationCellSection
                    evalOpen={evalOpen}
                    onAnalysisLoaded={(details) => {
                      // When the on-demand task completes, fold the
                      // freshly-fetched error_analysis into the local
                      // evalOpen state so the existing render path picks
                      // it up — same shape the dataset eval runner writes
                      // into cell.value_infos when error_localizer was
                      // enabled at run time.
                      setEvalOpen((prev) =>
                        prev
                          ? {
                              ...prev,
                              valueInfos: {
                                ...(prev.valueInfos || {}),
                                errorAnalysis: details?.error_analysis,
                                input_data: details?.input_data,
                                input_types: details?.input_types,
                                selected_input_key: details?.selected_input_key,
                              },
                            }
                          : prev,
                      );
                    }}
                  />
                )}
              </Box>
              <Box
                sx={{
                  display: "flex",
                  gap: 2,
                  width: "100%",
                  marginY: "15px",
                  position: "sticky",
                  paddingX: "15px",
                  bottom: 0,
                  backgroundColor: "background.paper",
                  zIndex: 1,
                }}
              >
                {evalOpen?.metadata?.runPrompt && (
                  <Button
                    variant="contained"
                    color="primary"
                    fullWidth
                    size="small"
                    onClick={() => {
                      setImprovePrompt({
                        ...column?.col,
                        ...datapoint,
                        rowData: datapoint?.rowData,
                      });
                      setEvalOpen(null);
                    }}
                    sx={{
                      fontSize: "14px",
                      height: "40px",
                    }}
                  >
                    Improve Prompt
                  </Button>
                )}
                {evalOutput !== OutputTypes.NUMERIC && !evalOpenIsCode && (
                  <Button
                    variant="contained"
                    color="primary"
                    fullWidth
                    size="small"
                    onClick={() => {
                      setAddEvaluationFeeback({
                        ...column?.col,
                        ...datapoint,
                        rowData: datapoint?.rowData,
                      });
                      setEvalOpen(null);
                      trackEvent(Events.datasetAddFeedbackClicked, {
                        [PropertyName.datasetId]: dataset,
                        [PropertyName.evalId]:
                          evalOpen?.evalMetricId || column?.headerName,
                        [PropertyName.rowIdentifier]: datapoint?.rowData?.rowId,
                      });
                    }}
                    sx={{
                      fontSize: "14px",
                      height: "40px",
                    }}
                  >
                    Add Feedback
                  </Button>
                )}
              </Box>
            </Box>
          </Collapse>
          <Box
            sx={{
              padding: "20px",
              gap: "20px",
              display: "flex",
              flexDirection: "column",
              height: "100%",
              width: "550px",
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                top: 0,
                position: "sticky",
                fontFamily: "IBM Plex Sans",
              }}
            >
              <Typography
                variant="m3"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                Datapoint-{datapoint?.index + 1}
              </Typography>
              <Box sx={{ display: "flex", alignItems: "center" }}>
                <Button
                  variant="soft"
                  size="small"
                  onClick={() => {
                    setAnnotateOpen((prev) => !prev);
                    if (!annotateOpen) setEvalOpen(null);
                  }}
                  startIcon={<Iconify icon="eva:edit-2-fill" width={16} />}
                  sx={{
                    marginRight: theme.spacing(1.5),
                    color: annotateOpen
                      ? "primary.contrastText"
                      : "text.secondary",
                    backgroundColor: annotateOpen
                      ? "primary.main"
                      : "action.hover",
                    fontWeight: "fontWeightMedium",
                    typography: "s1",
                  }}
                >
                  Annotate
                </Button>
                <Box sx={{ display: "flex", gap: 0.5, mr: 1 }}>
                  <NavIconButton
                    icon="mdi:chevron-up"
                    tooltip="Previous datapoint (K)"
                    onClick={() => onNavigate("previous")}
                    disabled={datapoint?.index === 0}
                  />
                  <NavIconButton
                    icon="mdi:chevron-down"
                    tooltip="Next datapoint (J)"
                    onClick={() => onNavigate("next")}
                    disabled={
                      datapoint?.index === totalRowCount - 1 ||
                      isLoadingNextItemIds ||
                      isLoadingCellData
                    }
                    loading={isLoadingNextItemIds || isLoadingCellData}
                  />
                </Box>
                <IconButton onClick={onClose} size="small">
                  <Iconify
                    icon="akar-icons:cross"
                    sx={{ color: "text.primary" }}
                  />
                </IconButton>
              </Box>
            </Box>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 2,
                overflowY: "auto",
              }}
            >
              <Box
                sx={{
                  minHeight: "220px",
                  maxHeight: "300px",
                  paddingBottom: 1,
                  width: "100%",
                }}
              >
                <div className="ag-theme-alpine" style={{ height: "100%" }}>
                  <AgGridReact
                    theme={agTheme}
                    columnDefs={TabColumnDefs}
                    defaultColDef={defaultColDef}
                    rowData={runEvalData}
                    suppressCellFocus={true}
                    suppressRowClickSelection={true}
                    domLayout="normal"
                    suppressRowDrag={true}
                  />
                </div>
              </Box>
              {[...allColumns].map((col) => {
                const key = col.field;
                const value = mainData[key];
                const isAudioColumn = audioColumnIds.includes(key);
                const isImageColumn = imageColumnIds.includes(key);
                const isImagesColumn = imagesColumnIds.includes(key);
                const isDocumentColumn = documentColumnIds.includes(key);
                if (col?.originType === "evaluation") {
                  return <></>;
                }
                return (
                  <div
                    key={key}
                    ref={(el) => {
                      if (el) columnRefToScroll.current[key] = el;
                    }}
                  >
                    {isAudioColumn ? (
                      value?.cellValue ? (
                        <AudioDatapointCard value={value} column={col} />
                      ) : (
                        <DatapointCard
                          value={{ cellValue: "No audio has been added" }}
                          column={col}
                          allowCopy={true}
                          isEmptyField={true}
                          sx={{
                            borderTop: "1px solid",
                            borderColor: "divider",
                            borderBottomLeftRadius: "8px",
                            borderBottomRightRadius: "8px",
                          }}
                          showTabs={col?.originType !== "OTHERS"}
                        />
                      )
                    ) : isImageColumn ? (
                      value?.cellValue ? (
                        <ImageDatapointCard value={value} column={col} />
                      ) : (
                        <DatapointCard
                          value={{ cellValue: "No image has been added" }}
                          column={col}
                          allowCopy={true}
                          isEmptyField={true}
                          sx={{
                            borderTop: "1px solid",
                            borderColor: "divider",
                            borderBottomLeftRadius: "8px",
                            borderBottomRightRadius: "8px",
                          }}
                          showTabs={col?.originType !== "OTHERS"}
                        />
                      )
                    ) : isImagesColumn ? (
                      value?.cellValue ? (
                        <ImagesDatapointCard value={value} column={col} />
                      ) : (
                        <DatapointCard
                          value={{ cellValue: "No images have been added" }}
                          column={col}
                          allowCopy={true}
                          isEmptyField={true}
                          sx={{
                            borderTop: "1px solid",
                            borderColor: "divider",
                            borderBottomLeftRadius: "8px",
                            borderBottomRightRadius: "8px",
                          }}
                          showTabs={col?.originType !== "OTHERS"}
                        />
                      )
                    ) : isDocumentColumn ? (
                      value?.cellValue ? (
                        <DocumentDatapointCard value={value} column={col} />
                      ) : (
                        <DatapointCard
                          value={{ cellValue: "No document has been added" }}
                          column={col}
                          allowCopy={true}
                          isEmptyField={true}
                          sx={{
                            borderTop: "1px solid",
                            borderColor: "divider",
                            borderBottomLeftRadius: "8px",
                            borderBottomRightRadius: "8px",
                          }}
                          showTabs={col?.originType !== "OTHERS"}
                        />
                      )
                    ) : (
                      <DatapointCard
                        value={value}
                        column={col}
                        allowCopy={true}
                        sx={{
                          borderTop:
                            col?.originType !== "OTHERS"
                              ? "1px solid"
                              : undefined,
                          borderColor:
                            col?.originType !== "OTHERS"
                              ? "divider"
                              : undefined,
                          borderBottomLeftRadius: "8px",
                          borderBottomRightRadius: "8px",
                        }}
                        showTabs={col?.originType !== "OTHERS"}
                      />
                    )}
                  </div>
                );
              })}

              {/* Existing annotations */}
              <ScoresListSection
                sourceType="dataset_row"
                sourceId={datapoint?.rowData?.rowId}
              />
            </Box>
          </Box>
        </Box>
      </Fade>

      <AddLabelDrawer
        open={addLabelDrawerOpen}
        onClose={() => setAddLabelDrawerOpen(false)}
        datasetId={dataset}
        onLabelsChanged={() => {
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues"],
          });
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues", "for-source"],
          });
        }}
      />
    </Box>
  );
};

DatapointDrawerChild.propTypes = {
  datapoint: PropTypes.object,
  setDataPointDrawerData: PropTypes.func,
  allColumns: PropTypes.array,
  onClose: PropTypes.func,
  setEvalDrawer: PropTypes.func,
  evalDrawer: PropTypes.bool,
  rowIndex: PropTypes.number,
  setRunEval: PropTypes.func,
  validatedFilters: PropTypes.array,
  setActiveRow: PropTypes.func,
  totalCount: PropTypes.number,
  runEval: PropTypes.object,
  rowNewData: PropTypes.object,
  currentColumn: PropTypes.object,
  setRowNewData: PropTypes.func,
  allRows: PropTypes.array,
  setAllRows: PropTypes.array,
};

/**
 * Renders the "Possible Error" section for an eval cell.
 *
 * Three states:
 *   1. Cell already has `errorAnalysis` (eval was run with localizer
 *      enabled at run time) → render the existing card.
 *   2. No errorAnalysis yet AND user hasn't requested one → show a
 *      "Run error localization" button.
 *   3. User requested or task is in flight → poll the cell endpoint
 *      every 3s until status is terminal, then bubble the analysis
 *      back to the parent via `onAnalysisLoaded`.
 */
const ErrorLocalizationCellSection = ({ evalOpen, onAnalysisLoaded }) => {
  const queryClient = useQueryClient();
  const cellId = evalOpen?.cell_id || evalOpen?.cellId || evalOpen?.id;
  // Keep a stable ref so the polling effect never needs the callback in its
  // deps — avoids an infinite loop when the parent passes an inline arrow.
  const onAnalysisLoadedRef = useRef(onAnalysisLoaded);
  useEffect(() => {
    onAnalysisLoadedRef.current = onAnalysisLoaded;
  }, [onAnalysisLoaded]);
  const valueInfos = evalOpen?.valueInfos;
  const inlineAnalysis = valueInfos?.errorAnalysis;
  const hasInlineAnalysis = !!(
    inlineAnalysis &&
    typeof inlineAnalysis === "object" &&
    Object.values(inlineAnalysis).some((v) => Array.isArray(v) && v.length > 0)
  );

  const [requested, setRequested] = useState(false);
  const [activeTaskId, setActiveTaskId] = useState(null);

  const { data: pollData } = useQuery({
    queryKey: ["cell-error-localizer", cellId, requested],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getCellErrorLocalizer(cellId),
      );
      return data?.result || null;
    },
    enabled: !!cellId && !hasInlineAnalysis,
    refetchInterval: (q) => {
      const r = q?.state?.data;
      if (!r) return false;
      const status = r.status;
      // After re-run, backend can return the previous completed task once.
      // Keep polling until the newly started task_id appears.
      if (activeTaskId && r.task_id !== activeTaskId) return 3000;
      // Keep polling while pending/running.
      if (status === "pending" || status === "running") return 3000;
      return false;
    },
    refetchOnWindowFocus: false,
  });

  // When the task transitions to completed, push the analysis upward
  // and stop the poll.
  useEffect(() => {
    if (!pollData) return;
    if (pollData.status === "completed" && pollData.error_analysis) {
      onAnalysisLoadedRef.current?.({
        error_analysis: pollData.error_analysis,
        selected_input_key: pollData.selected_input_key,
        input_data: pollData.input_data,
        input_types: pollData.input_types,
      });
    }
    // If we found an in-flight task on mount (without the user having
    // clicked the button), surface the running banner by flipping
    // requested.
    if (
      !requested &&
      (pollData.status === "pending" || pollData.status === "running")
    ) {
      setRequested(true);
    }
  }, [pollData, requested]);

  // Trigger mutation
  const triggerMutation = useMutation({
    mutationFn: async () => {
      const { data } = await axios.post(
        endpoints.develop.eval.runCellErrorLocalizer(cellId),
        {},
      );
      return data?.result;
    },
    onSuccess: (res) => {
      setActiveTaskId(res?.task_id || null);
      setRequested(true);
      // Invalidate the poll query so it kicks off immediately.
      queryClient.invalidateQueries({
        queryKey: ["cell-error-localizer", cellId],
      });
    },
    onError: () => {
      setRequested(false);
      setActiveTaskId(null);
    },
  });

  const renderAnalysis =
    inlineAnalysis ||
    (pollData?.status === "completed" ? pollData?.error_analysis : null);
  const renderAnalysisEntries = canonicalEntries(renderAnalysis || {}).filter(
    ([_, value]) => Array.isArray(value) && value.length > 0,
  );
  const hasRenderAnalysis = renderAnalysisEntries.length > 0;
  const renderAnalysisForDisplay = Object.fromEntries(renderAnalysisEntries);
  const renderSelectedInputKey =
    valueInfos?.selected_input_key || pollData?.selected_input_key;
  const renderValueInfos = {
    ...(valueInfos || {}),
    errorAnalysis: renderAnalysisForDisplay,
    selected_input_key: renderSelectedInputKey,
    input_data: valueInfos?.input_data || pollData?.input_data,
    input_types: valueInfos?.input_types || pollData?.input_types,
  };

  // Branch 1: existing inline analysis — render via the original path.
  if (hasRenderAnalysis) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          height: "100%",
          marginY: "15px",
          overflowWrap: "break-word",
          position: "relative",
        }}
      >
        <Typography
          fontWeight="fontWeightMedium"
          variant="s1"
          color="text.primary"
        >
          Possible Error
        </Typography>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            overflowY: "auto",
          }}
        >
          {(() => {
            const hasOrgSegment = renderAnalysisEntries
              .flatMap(([_, value]) => value)
              .some((entry) => entry?.orgSegment);
            if (hasOrgSegment) {
              return (
                <AudioErrorCard
                  valueInfos={renderValueInfos}
                  column={renderSelectedInputKey}
                />
              );
            }
            return renderAnalysisEntries.map(([key, value]) => (
              <ErrorLocalizeCard
                key={key}
                value={value}
                column={renderSelectedInputKey}
                tabValue="raw"
                datapoint={renderValueInfos}
              />
            ));
          })()}
        </Box>
      </Box>
    );
  }

  if (!cellId) return null;

  const status = pollData?.status;
  const waitingForActiveTask =
    !!activeTaskId && pollData?.task_id !== activeTaskId;
  const isRunning =
    status === "pending" || status === "running" || waitingForActiveTask;
  const isFailed = status === "failed";
  const isSkipped = status === "skipped";
  const isCompleted = status === "completed";

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        marginY: "15px",
      }}
    >
      <Typography
        fontWeight="fontWeightMedium"
        variant="s1"
        color="text.primary"
      >
        Possible Error
      </Typography>
      {isRunning ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: "primary.main",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(124, 77, 255, 0.08)"
                : "rgba(124, 77, 255, 0.04)",
          }}
        >
          <CircularProgress size={14} thickness={5} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ display: "block", color: "primary.main" }}
            >
              Error localization running…
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", fontSize: "11px" }}
            >
              Pinpointing which parts of the input caused the failure. Usually
              30–90 seconds.
            </Typography>
          </Box>
        </Box>
      ) : isFailed ? (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: "error.light",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255, 86, 48, 0.08)"
                : "rgba(255, 86, 48, 0.04)",
          }}
        >
          <Typography variant="caption" fontWeight={600} color="error.main">
            Error localization failed
          </Typography>
          {pollData?.error_message && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "11px" }}
            >
              {pollData.error_message}
            </Typography>
          )}
          <Box>
            <Button
              size="small"
              variant="outlined"
              color="primary"
              onClick={() => triggerMutation.mutate()}
              disabled={triggerMutation.isPending}
              sx={{ textTransform: "none", fontSize: "12px", mt: 0.5 }}
            >
              Retry
            </Button>
          </Box>
        </Box>
      ) : isSkipped ? (
        <Typography variant="caption" color="text.secondary">
          Error localization was skipped — input data isn&apos;t available to
          localize on.
        </Typography>
      ) : (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            border: "1px dashed",
            borderColor: "divider",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.02)"
                : "rgba(0,0,0,0.02)",
          }}
        >
          <Iconify
            icon="solar:target-bold"
            width={18}
            sx={{ color: "primary.main", flexShrink: 0 }}
          />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ display: "block" }}
            >
              No error localization yet
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", fontSize: "11px" }}
            >
              Pinpoint which parts of the input caused this eval to fail.
            </Typography>
          </Box>
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={triggerMutation.isPending}
            onClick={() => triggerMutation.mutate()}
            sx={{
              textTransform: "none",
              fontSize: "12px",
              flexShrink: 0,
              height: 30,
            }}
          >
            Run
          </LoadingButton>
        </Box>
      )}
      {isCompleted && (
        <Typography variant="caption" color="text.secondary">
          Error localization completed — no error segments were found for this
          input.
        </Typography>
      )}
      {triggerMutation.isError && (
        <Typography
          variant="caption"
          color="error.main"
          sx={{ fontSize: "11px" }}
        >
          {triggerMutation.error?.response?.data?.result ||
            triggerMutation.error?.message ||
            "Failed to start error localization."}
        </Typography>
      )}
    </Box>
  );
};

ErrorLocalizationCellSection.propTypes = {
  evalOpen: PropTypes.object,
  onAnalysisLoaded: PropTypes.func,
};

const DatapointDrawerV2 = () => {
  const { datapoint, setDatapoint, setDrawerColumn } =
    useDatapointDrawerStore();

  const onClose = () => {
    setDatapoint(null);
    setDrawerColumn(null);
  };

  return (
    <Drawer
      anchor="right"
      open={Boolean(datapoint)}
      variant="temporary"
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 10,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <DatapointDrawerChild />
    </Drawer>
  );
};

export default DatapointDrawerV2;
