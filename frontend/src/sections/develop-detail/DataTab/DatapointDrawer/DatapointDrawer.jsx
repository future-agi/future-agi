import {
  Box,
  Button,
  Chip,
  Collapse,
  Drawer,
  IconButton,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import DatapointCard from "../../../common/DatapointCard";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";

import AudioDatapointCard from "src/components/custom-audio/AudioDatapointCard";
import { getStatusColor } from "../common";
import { getLabel } from "../common";
import AudioErrorCard from "src/components/custom-audio/AudioErrorCard";
import { ShowComponent } from "src/components/show";
import ImageDatapointCard from "src/sections/common/ImageDatapointCard";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

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

const StatusCellRenderer = (data) => {
  const theme = useTheme();

  let cellValue = data?.data?.data?.cellValue;
  const status = data?.data?.status;
  const statusInCreation = data?.data?.isLoading;

  if (status === "running" || statusInCreation) return <SkeletonLoader />;
  if (status === "error") {
    return (
      <Box
        sx={{
          marginLeft: theme.spacing(1),
          color: theme.palette.error.main,
          fontSize: "13px",
        }}
      >
        Error
      </Box>
    );
  }

  if (cellValue?.startsWith("[") && cellValue?.endsWith("]")) {
    cellValue = JSON.parse(cellValue.replace(/'/g, '"'));
  }
  if (!hasRenderableCellValue(cellValue)) return;

  return (
    <>
      <ShowComponent condition={!Array.isArray(cellValue)}>
        <Chip
          variant="soft"
          label={getLabel(cellValue)}
          size="small"
          sx={{
            ...getStatusColor(cellValue, theme),
            transition: "none",
            "&:hover": {
              backgroundColor: getStatusColor(cellValue, theme).backgroundColor, // Lock it to same color
              boxShadow: "none",
            },
          }}
        />
      </ShowComponent>
      <ShowComponent condition={Array.isArray(cellValue)}>
        <ShowComponent condition={cellValue.length === 0}>
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
                backgroundColor: theme.palette.red.o10, // Lock it to same color
                boxShadow: "none",
              },
            }}
          />
        </ShowComponent>
        <ShowComponent condition={cellValue.length > 0}>
          <Box>
            <Chip
              variant="soft"
              label={getLabel(cellValue)}
              size="small"
              sx={{
                ...getStatusColor(data?.data?.data?.cellValue, theme),
                marginRight: "10px",
                transition: "none",
                "&:hover": {
                  backgroundColor: getStatusColor(
                    data?.data?.data?.cellValue,
                    theme,
                  ).backgroundColor, // Lock it to same color
                  boxShadow: "none",
                },
              }}
            />
            {cellValue.length > 1 && (
              <Chip
                variant="soft"
                label={`+${cellValue.length - 1}`}
                size="small"
                sx={{
                  ...getStatusColor(data?.data?.data?.cellValue, theme),
                  transition: "none",
                  "&:hover": {
                    backgroundColor: getStatusColor(
                      data?.data?.data?.cellValue,
                      theme,
                    ).backgroundColor, // Lock it to same color
                    boxShadow: "none",
                  },
                }}
              />
            )}
          </Box>
        </ShowComponent>
      </ShowComponent>
    </>
  );
};

const ViewDetailsCellRenderer = (props) => {
  const { data = {}, node, setEvalDrawer, setRunEval, disabled } = props;
  const theme = useTheme();

  const handleClick = () => {
    const metadata = data?.data?.column?.col?.metadata;
    if (!disabled) {
      setEvalDrawer(true);
      setRunEval({
        ...node?.data?.data?.description,
        evalName: node?.data?.data?.eval_name,
        metadata: metadata,
        evalMetricId: data?.data?.column?.col?.sourceId,
      });
    }
  };

  return (
    <a
      style={{
        color: disabled
          ? theme.palette.text.disabled
          : theme.palette.primary.main,
        cursor: disabled ? "not-allowed" : "pointer",
        pointerEvents: disabled ? "none" : "auto",
      }}
      onClick={handleClick}
    >
      View Detail
    </a>
  );
};

const DATAPOINT_DRAWER_THEME_PARAMS = {
  headerColumnBorder: { width: "0px" },
  headerBackgroundColor: "whiteSpace.50",
  headerFontFamily: "IBM Plex Sans",
  headerFontWeight: "fontWeightMedium",
  headerFontSize: "14px",
};

const DatapointDrawerChild = ({
  datapoint,
  setDataPointDrawerData,
  allColumns,
  onClose,
  setEvalDrawer,
  evalDrawer,
  rowIndex,
  setRunEval,
  runEval,
  setActiveRow,
  totalCount,
  currentColumn,
  setRowNewData,
  allRows,
  setAllRows,
}) => {
  const { dataset } = useParams();
  const [nextAction, setNextAction] = useState(null);
  const [, setDisableNext] = useState({
    previous: false,
    next: false,
  });
  const theme = useTheme();
  const agTheme = useAgThemeWith(DATAPOINT_DRAWER_THEME_PARAMS);
  const [loading, setLoading] = useState(true);
  const [nextId, setNextId] = useState(null);

  useEffect(() => {
    setLoading(true);
    setTimeout(() => setLoading(false), 300);
  }, [datapoint]);

  const audioColumnIds = allColumns
    .filter((col) => col.dataType === "audio")
    .map((col) => col.id || col.field);

  const imageColumnIds = allColumns
    .filter((col) => col.dataType === "image")
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
      field: "cellValue",
      flex: 1,
      cellRenderer: (params) =>
        loading ? <SkeletonLoader /> : <StatusCellRenderer {...params} />,
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
            setRunEval={setRunEval}
            setEvalDrawer={setEvalDrawer}
            disabled={params.data?.isLoading}
          />
        ),
    },
  ];

  const defaultColDef = {
    lockVisible: true,
    sortable: true,
    filter: false,
    resizable: true,
    suppressHeaderMenuButton: true,
    suppressHeaderContextMenu: true,
  };

  const STATUS = {
    RUNNING: "running",
    ERROR: "error",
  };

  const runEvalData = useMemo(() => {
    const evalColumns = allColumns.filter((i) => i.originType === "evaluation");
    const currentRowData = datapoint?.rowData ? datapoint?.rowData : [];

    const valueInfos = evalColumns.map((column) => {
      const columnId = column.field;
      const rowDataForColumn = currentRowData?.[columnId];

      const baseData = {
        data: {
          column: column,
          evalName: column.headerName,
          description: rowDataForColumn ?? null,
          cellValue: rowDataForColumn?.cellValue ?? null,
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
  }, [rowIndex, datapoint]);

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

  const updateRowFromApiResult = (rowId, apiResult) => {
    const apiDataForRow = apiResult[rowId];
    if (!apiDataForRow) return;

    setAllRows((prev) =>
      prev.map((row) => {
        if (row.data.rowId === rowId) {
          return {
            ...row,
            data: {
              rowId: rowId,
              ...apiDataForRow, // the actual cell objects
            },
          };
        }
        return row;
      }),
    );
  };

  const { mutate: getCellData, isPending } = useMutation({
    mutationFn: (d) => {
      return axios.post(endpoints.develop.getCellData, d);
    },
    onSuccess: (data, variables) => {
      updateRowFromApiResult(variables.row_ids[0], data?.data?.result);

      const next = allRows.find((i) => i?.data?.rowId == variables?.row_ids[0]);
      const nextIndex = rowIndex + 1;
      setNextId(allRows[nextIndex + 1]?.data?.rowId);
      if (next && datapoint.id) {
        setDataPointDrawerData((pre) => {
          return {
            ...pre,
            index: nextIndex,
            rowIndexData: nextIndex,
            rowData: {
              rowId: variables.row_ids[0],
              ...data?.data?.result[variables.row_ids[0]],
            },
            valueInfos: next[pre.id]?.valueInfos,
          };
        });
        setRowNewData({ current: next });
        setActiveRow(nextIndex);
        setDisableNext(() => ({
          next: false,
          previous: false,
        }));
      } else {
        setDisableNext(() => ({
          next: nextAction,
          previous: !nextAction,
        }));
        enqueueSnackbar({
          message: "No more datapoint available",
          variant: "error",
        });
      }
      setDisableNext(() => ({
        next: Boolean(!data?.data?.result?.next),
        previous: !nextAction,
      }));
    },
  });

  const { mutate: setNextItem } = useMutation({
    mutationFn: (d) => {
      return axios.post(endpoints.develop.getRowData(dataset), d);
    },
    onSuccess: (data) => {
      if (data?.data?.result?.next?.rowId) {
        const newIds = data?.data?.result?.next?.rowId;
        setNextId(newIds?.length > 0 ? newIds[0] : null);
        setAllRows((prev) => {
          const mergedMap = new Map();

          prev.forEach((row) => {
            const id = row?.rowIndex;
            if (id !== undefined) {
              mergedMap.set(id, row);
            }
          });

          const totalLength = allRows?.length;

          newIds.forEach((row, index) => {
            const id = row;
            if (id !== undefined) {
              mergedMap.set(row, {
                rowIndex: totalLength + index,
                data: { rowId: row },
              });
            }
          });

          return Array.from(mergedMap.values());
        });
      }
    },
  });

  const loadDatapoint = (direction) => {
    // const payload = {
    //   row_id: datapoint?.rowData?.rowId,
    //   filters: validatedFilters,
    // };
    setNextAction(direction === "next");

    const next = direction == "next" ? "next" : "previous";
    const nextIndex = direction == "next" ? rowIndex + 1 : rowIndex - 1;
    const nextData = allRows.find((i) => i.rowIndex == nextIndex)?.data;

    if (!nextId && allRows.length > 10) {
      setNextId(allRows[allRows?.length - 1]?.data?.rowId);
    }

    if (nextIndex == allRows.length - 1 && allRows.length > 10) {
      setNextItem({ row_id: nextId });
    }

    if (next != "previous" && Object.entries(nextData ?? {}).length == 1) {
      const payload = {
        row_ids: [nextId],
        column_ids: allColumns.map((i) => i?.col?.id),
      };
      getCellData(payload);
      return;
    }

    if (next && datapoint.id && nextData) {
      setDataPointDrawerData((pre) => {
        return {
          ...pre,
          index: nextIndex,
          rowIndexData: nextIndex,
          rowData: nextData,
          valueInfos: nextData[pre.id]?.valueInfos,
        };
      });
      setRowNewData({ current: nextData });
      setActiveRow(nextIndex);
      setDisableNext(() => ({
        next: false,
        previous: false,
      }));
    }
  };

  const isPrevDisabled = rowIndex <= 0;
  const isNextDisabled = rowIndex >= totalCount - 1;

  const finalArray = useMemo(() => {
    if (
      runEval?.cellValue &&
      runEval?.cellValue.startsWith("[") &&
      runEval?.cellValue.endsWith("]")
    ) {
      return JSON.parse(runEval?.cellValue.replace(/'/g, '"'));
    }
  }, [runEval?.cellValue]);

  return (
    <Box sx={{ display: "flex", height: "100vh", justifyContent: "flex-end" }}>
      <Collapse
        in={evalDrawer}
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
              {runEval?.eval_name || currentColumn?.headerName}
            </Typography>
            <Button
              variant="soft"
              size="small"
              sx={{ backgroundColor: "action.hover", color: "text.secondary" }}
              onClick={() => setEvalDrawer(false)}
            >
              Close
            </Button>
          </Box>
          {/* </Box> */}
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
              <Typography fontWeight={500} fontSize={14} color="text.primary">
                Score
              </Typography>
              <Box>
                {runEval?.status === "error" ? (
                  <Box
                    sx={{ color: theme.palette.error.main, fontSize: "14px" }}
                  >
                    Error
                  </Box>
                ) : (
                  hasRenderableCellValue(runEval?.cellValue) && (
                    <>
                      <ShowComponent condition={!Array.isArray(finalArray)}>
                        <Chip
                          variant="soft"
                          label={getLabel(runEval?.cellValue)}
                          size="small"
                          sx={{
                            ...getStatusColor(runEval?.cellValue, theme),
                            transition: "none",
                            "&:hover": {
                              backgroundColor: getStatusColor(
                                runEval?.cellValue,
                                theme,
                              ).backgroundColor, // Lock it to same color
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
                                backgroundColor: theme.palette.red[500], // Lock it to same color
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
                                ...getStatusColor(runEval?.cellValue, theme),
                                marginRight: theme.spacing(1),
                                transition: "none",
                                "&:hover": {
                                  backgroundColor: getStatusColor(
                                    runEval?.cellValue,
                                    theme,
                                  ).backgroundColor, // Lock it to same color
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
              <Typography fontWeight={500} fontSize={14} color="text.primary">
                Explanation
              </Typography>
              <Box
                sx={{
                  border: "1px solid var(--border-default)",
                  padding: "16px",
                  borderRadius: "4px",
                }}
              >
                {runEval?.valueInfos?.reason?.trim() ? (
                  <CellMarkdown
                    spacing={0}
                    text={runEval?.valueInfos?.reason}
                  />
                ) : (
                  "Unable to fetch Explanation"
                )}
              </Box>
            </Box>
            <ShowComponent
              condition={
                runEval?.valueInfos?.errorAnalysis &&
                runEval?.valueInfos?.errorAnalysis?.input1?.length
              }
            >
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
                  fontWeight={"fontWeightMedium"}
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
                  {runEval &&
                    typeof runEval?.valueInfos === "object" &&
                    runEval?.valueInfos?.errorAnalysis &&
                    (() => {
                      const hasOrgSegment = Object.values(
                        runEval?.valueInfos?.errorAnalysis,
                      )
                        .flat()
                        .some((entry) => entry?.orgSegment);

                      if (hasOrgSegment) {
                        return (
                          <AudioErrorCard
                            valueInfos={runEval?.valueInfos}
                            column={runEval?.valueInfos?.selectedInputKey}
                          />
                        );
                      }

                      return Object.entries(runEval?.valueInfos?.errorAnalysis)
                        .filter(([_, value]) => value?.length)
                        .map(([key, value]) => (
                          <ErrorLocalizeCard
                            key={key}
                            value={value}
                            column={runEval?.valueInfos?.selectedInputKey}
                            tabValue="raw"
                            datapoint={runEval?.valueInfos}
                          />
                        ));
                    })()}
                </Box>
              </Box>
            </ShowComponent>
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
            {runEval?.metadata?.runPrompt && (
              <Button
                variant="contained"
                color="primary"
                fullWidth
                size="small"
                onClick={() => {
                  datapoint.improvementClick({
                    ...datapoint,
                    rowData: datapoint?.rowData,
                  });
                  setEvalDrawer(false);
                  setDataPointDrawerData(false);
                }}
                sx={{
                  fontSize: "14px",
                  height: "40px",
                }}
              >
                Improve Prompt
              </Button>
            )}
            <Button
              variant="contained"
              color="primary"
              fullWidth
              size="small"
              onClick={() => {
                datapoint.feedBackClick({
                  ...datapoint,
                  rowData: datapoint?.rowData,
                  sourceId: runEval?.evalMetricId
                    ? runEval?.evalMetricId
                    : datapoint?.sourceId,
                  valueInfos: runEval?.valueInfos
                    ? runEval?.valueInfos
                    : datapoint?.valueInfos,
                  name: runEval?.valueInfos?.name
                    ? runEval?.valueInfos?.name
                    : datapoint?.name,
                });
                setEvalDrawer(false);
                setDataPointDrawerData(false);
                trackEvent(Events.datasetAddFeedbackClicked, {
                  [PropertyName.datasetId]: dataset,
                  [PropertyName.evalId]:
                    runEval?.evalMetricId || currentColumn?.col?.sourceId,
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
            Datapoint-{rowIndex + 1}
          </Typography>
          <Box>
            <LoadingButton
              type="button"
              loading={!nextAction && isPending}
              onClick={() => loadDatapoint("prev")}
              variant="outlined"
              disabled={isPrevDisabled}
              size="small"
              sx={{
                marginRight: theme.spacing(1),
                paddingLeft: "15px",
                borderColor: theme.palette.divider,
                color: theme.palette.text.secondary,
                fontWeight: "fontWeightRegular",
                typography: "s1",
              }}
            >
              Prev
              <Iconify
                sx={{
                  marginLeft: theme.spacing(1),
                  color: theme.palette.text.secondary,
                }}
                icon="akar-icons:chevron-up-small"
              />
            </LoadingButton>
            <LoadingButton
              type="button"
              loading={nextAction && isPending}
              onClick={() => loadDatapoint("next")}
              variant="outlined"
              disabled={isNextDisabled}
              size="small"
              sx={{
                marginRight: theme.spacing(1),
                paddingLeft: "15px",
                borderColor: theme.palette.divider,
                color: theme.palette.text.secondary,
                fontWeight: "fontWeightRegular",
                typography: "s1",
              }}
            >
              Next
              <Iconify
                sx={{
                  marginLeft: theme.spacing(1),
                  color: theme.palette.text.secondary,
                }}
                icon="akar-icons:chevron-down-small"
              />
            </LoadingButton>
            <IconButton onClick={onClose} size="small">
              <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
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
            if (col?.originType === "evaluation") {
              return <></>;
            }
            return (
              <div key={key}>
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
                    />
                  )
                ) : (
                  <DatapointCard
                    value={value}
                    column={col}
                    allowCopy={true}
                    sx={{
                      borderTop: "1px solid",
                      borderColor: "divider",
                      borderBottomLeftRadius: "8px",
                      borderBottomRightRadius: "8px",
                    }}
                  />
                )}
              </div>
            );
          })}
        </Box>
      </Box>
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

const DatapointDrawer = ({
  open,
  onClose,
  datapoint,
  setDataPointDrawerData,
  allColumns,
  rowIndex,
  setEvalDrawer,
  evalDrawer,
  validatedFilters,
  setActiveRow,
  totalCount,
  rowNewData,
  currentColumn,
  setRowNewData,
  allRows,
  setAllRows,
}) => {
  const [runEval, setRunEval] = useState(null);

  useEffect(() => {
    if (currentColumn?.originType === "evaluation" && datapoint?.rowData) {
      const currentField = datapoint?.rowData[currentColumn?.field];
      const isRunning = currentField?.status === "running";

      setEvalDrawer(!(isRunning || !currentField));
      setRunEval({
        ...currentField,
        metadata: datapoint.metadata,
      });
    } else {
      setEvalDrawer(false);
    }
  }, [datapoint, datapoint?.rowData, currentColumn]);

  return (
    <Drawer
      anchor="right"
      open={open}
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
      <DatapointDrawerChild
        datapoint={datapoint}
        allColumns={allColumns}
        onClose={onClose}
        setEvalDrawer={setEvalDrawer}
        evalDrawer={evalDrawer}
        rowIndex={rowIndex}
        setRunEval={setRunEval}
        runEval={runEval}
        setDataPointDrawerData={setDataPointDrawerData}
        validatedFilters={validatedFilters}
        setActiveRow={setActiveRow}
        totalCount={totalCount}
        rowNewData={rowNewData}
        currentColumn={currentColumn}
        setRowNewData={setRowNewData}
        allRows={allRows}
        setAllRows={setAllRows}
      />
    </Drawer>
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

DatapointDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  setDataPointDrawerData: PropTypes.func,
  datapoint: PropTypes.object,
  allColumns: PropTypes.array,
  rowIndex: PropTypes.number,
  setEvalDrawer: PropTypes.func,
  evalDrawer: PropTypes.bool,
  validatedFilters: PropTypes.array,
  setActiveRow: PropTypes.func,
  totalCount: PropTypes.number,
  rowNewData: PropTypes.object,
  currentColumn: PropTypes.object,
  setRowNewData: PropTypes.func,
  allRows: PropTypes.array,
  setAllRows: PropTypes.array,
};

StatusCellRenderer.propTypes = {
  value: PropTypes.string,
};

export default DatapointDrawer;
