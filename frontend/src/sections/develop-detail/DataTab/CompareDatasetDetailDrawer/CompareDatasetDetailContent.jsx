import EmptyContent from "src/components/empty-content";
import {
  Box,
  Chip,
  IconButton,
  Skeleton,
  Stack,
  Switch,
  Typography,
  useTheme,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import DatapointCard from "src/sections/common/DatapointCard";
import AudioDatapointCard from "src/components/custom-audio/AudioDatapointCard";
import ImagesDatapointCard from "src/sections/common/ImagesDatapointCard";
import { getUniqueColorPalette } from "src/utils/utils";
import ExperimentDescriptionCell from "src/sections/experiment-detail/ExperimentData/ExperimentDescriptionCell";
import ViewDetailsModal from "src/sections/experiment-detail/ExperimentData/ViewEvalDetailsModal";
import { getLabel, getStatusColor } from "../common";
import CompareDatasetEvalsAddFeedbackForm from "./CompareDatasetEvalsAddFeedbackForm";
import { LoadingButton } from "@mui/lab";

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

export const StatusCellRenderer = (props) => {
  const theme = useTheme();
  const { value } = props;
  let cellValue = value?.cellValue;
  const status = value?.status;

  if (status === "running") return <SkeletonLoader />;
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

  if (cellValue?.startsWith("['") && cellValue?.endsWith("']")) {
    cellValue = JSON.parse(cellValue.replace(/'/g, '"'));
  }
  if (
    !cellValue ||
    cellValue === "[]" ||
    cellValue === undefined ||
    cellValue === ""
  )
    return "-";

  return (
    <Box>
      <Chip
        variant="soft"
        label={getLabel(cellValue)}
        size="small"
        sx={{
          ...getStatusColor(cellValue, theme),
          marginRight: "10px",
          transition: "none",
          "&:hover": {
            backgroundColor: getStatusColor(cellValue, theme).backgroundColor, // Lock it to same color
            boxShadow: "none",
          },
        }}
      />

      {Array.isArray(cellValue) && cellValue.length > 1 && (
        <Chip
          variant="soft"
          label={`+${cellValue.length - 1}`}
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
      )}
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  value: PropTypes.string,
  data: PropTypes.any,
};

const defaultColDef = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

export default function CompareDatasetDetailContent({
  onClose,
  row,
  columnConfig,
  showDiff,
  setShowDiff,
  handleToggleDiff,
  nextRowId,
  prevRowId,
  handleFetchNextRow,
  handleFetchPrevRow,
  isPending,
}) {
  const [height, setHeight] = useState(250);
  const [indColsDifTracker, setIndColsDifTracker] = useState({});
  const [selectedEvalDetail, setSelectedEvalDetail] = useState(null);
  const [openEvalDetailDrawer, setOpenEvalDetailDrawer] = useState(false);
  const [activeTab, setActiveTab] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const theme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const [openfeedbackForm, setOpenfeedbackForm] = useState(false);

  const { indColsHash, datasetCols } = useMemo(() => {
    const individualCols = [];
    let datasetCols = [];

    const datasets = columnConfig?.filter(
      (col) => col?.name !== col?.group?.name,
    );
    // remove evaluation
    const evalsRemoved = datasets?.filter(
      (col) => col?.group?.origin == "Dataset",
    );
    if (evalsRemoved && evalsRemoved?.length > 0) {
      individualCols.push(...evalsRemoved);
    }

    const indColsHash = {};
    if (individualCols && individualCols?.length > 0) {
      for (const col of individualCols) {
        if (indColsHash[col?.datasetId]) {
          indColsHash[col?.datasetId].push(col);
        } else {
          indColsHash[col?.datasetId] = [col];
        }
      }
    }

    const datasetValueHash = {};
    const cols = [];
    if (datasets && datasets?.length > 0) {
      for (const dataset of datasets) {
        if (datasetValueHash[dataset?.datasetId]) {
          cols.push(dataset);
        } else {
          datasetValueHash[dataset?.datasetId] = dataset;
        }
      }
    }

    datasetCols = Object.values(datasetValueHash) ?? [];

    return { indColsHash, datasetCols };
  }, [columnConfig]);

  const TabColumnDefs = [
    {
      headerName: "Evalutation Metrics",
      field: "group.name",
      flex: 1,
      minWidth: 150,
    },
    {
      headerName: "Score",
      flex: 1,
      minWidth: 100,
      cellRenderer: StatusCellRenderer,
      cellStyle: {
        display: "flex",
        alignItems: "center",
      },
      valueGetter: (params) => {
        return row?.[params?.data?.id];
      },
    },
    {
      headerName: "Description",
      field: "id",
      flex: 1,
      valueGetter: () => "View Description",
      cellRenderer: ExperimentDescriptionCell,
      resizable: false,
      minWidth: 100,
    },
  ];

  const evalsData = useMemo(() => {
    const datasetEvaludations = {};
    const evaluations = columnConfig?.filter(
      (i) => i?.originType == "evaluation",
    );
    if (evaluations && evaluations?.length > 0) {
      for (const item of evaluations) {
        if (datasetEvaludations[item?.datasetId]) {
          datasetEvaludations[item?.datasetId].push(item);
        } else {
          datasetEvaludations[item?.datasetId] = [item];
        }
      }
    }
    return datasetEvaludations;
  }, [columnConfig]);

  useEffect(() => {
    if (isPending) return;
    if (datasetCols?.length > 0) {
      const diffTracker = {};

      for (const datasetCol of datasetCols) {
        for (const indCOls of indColsHash?.[datasetCol?.datasetId] ?? []) {
          if (
            row?.[indCOls?.id]?.cellDiffValue &&
            row?.[indCOls?.id]?.cellDiffValue?.length > 0
          ) {
            diffTracker[indCOls?.id] = showDiff;
          }
        }
      }

      setIndColsDifTracker(diffTracker);
    }
  }, [datasetCols, row, isPending, indColsHash, showDiff]);

  const handleToggleDiffForSingleCol = (datasetColId, tabValue) => {
    const copy = { ...indColsDifTracker };
    if (datasetColId in copy) {
      copy[datasetColId] = Boolean(tabValue === "difference");
    }

    if (Object.keys(copy).length < 1) return;
    const isAllDiffOn = Object.values(copy).every((val) => {
      return val === true;
    });

    setShowDiff(isAllDiffOn);
    setIndColsDifTracker(copy);
    setActiveTab(isAllDiffOn ? "difference" : "");
    return;
  };

  useEffect(() => {
    const copy = { ...indColsDifTracker };
    if (Object.keys(copy).length < 1) return;

    const isAllDiffOn = Object.values(copy).every((val) => {
      return val === true;
    });

    setShowDiff(isAllDiffOn);
  }, [indColsDifTracker, setShowDiff]);

  const resizableRowRef = useRef(null);

  const handleMouseDown = (e) => {
    e.preventDefault();
    setTimeout(() => {
      setIsDragging(true);
    }, 0);
  };

  useEffect(() => {
    if (!isDragging) return;
    if (!resizableRowRef.current) return;

    const handleMouseMove = (e) => {
      if (!resizableRowRef.current) return;
      const rect = resizableRowRef.current.getBoundingClientRect();
      let newHeight = e.clientY - rect.y;
      newHeight = Math.max(0, Math.round(newHeight));
      setHeight(newHeight);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging]);

  const handleOpenFeedbackForm = () => {
    setOpenfeedbackForm(true);
  };

  const handleCLoseFeedbackForm = () => {
    setOpenfeedbackForm(false);
  };

  const CustomNoRowsOverlay = () => (
    <EmptyContent title="No output to apply evaluations" />
  );

  const isTwoDataset = datasetCols?.length === 2;
  return (
    <>
      <Box
        sx={{
          width: "100vw",
          padding: "16px",
          height: "100%",
          display: "flex",
          gap: 1,
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography variant="m3" fontWeight={600} color="text.primary">
            Datapoint-compare
          </Typography>
          <Stack direction={"row"} gap={"12px"} alignItems={"center"}>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Switch
                size="small"
                color="success"
                checked={showDiff}
                onChange={() => {
                  setIndColsDifTracker((prev) => {
                    const updated = {};
                    for (const key in prev) {
                      updated[key] = !showDiff;
                    }
                    return updated;
                  });
                  if (showDiff) {
                    setActiveTab("raw");
                  } else {
                    setActiveTab("");
                  }
                  handleToggleDiff();
                }}
              />
              <Typography variant="caption" fontWeight={400}>
                Show Diff
              </Typography>
              <CustomTooltip
                show
                title="Shows the difference between original column vs configured columns of the dataset"
                placement="bottom"
                arrow
              >
                <Iconify
                  width={16}
                  icon="solar:info-circle-linear"
                  sx={{ color: "text.secondary", marginLeft: 0.5 }}
                />
              </CustomTooltip>
            </Box>
            <LoadingButton
              loading={isPending}
              onClick={handleFetchPrevRow}
              disabled={Boolean(!prevRowId || isPending)}
              size="small"
              sx={{
                border: "1px solid",
                borderColor: "action.hover",
                "&.Mui-disabled": {
                  opacity: 0.4,
                },
              }}
            >
              <Typography
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightRegular"}
              >
                Prev
              </Typography>
              <Iconify
                sx={{
                  marginLeft: "8px",
                  color: "text.primary",
                  fontWeight: "fontWeightRegular",
                }}
                icon="solar:alt-arrow-up-linear"
              />
            </LoadingButton>
            <LoadingButton
              loading={isPending}
              onClick={handleFetchNextRow}
              disabled={Boolean(!nextRowId || isPending)}
              size="small"
              sx={{
                border: "1px solid",
                borderColor: "action.hover",
                "&.Mui-disabled": {
                  opacity: 0.4,
                },
              }}
            >
              <Typography
                variant="s1"
                color={"text.primary"}
                fontWeight={"fontWeightRegular"}
              >
                Next
              </Typography>
              <Iconify
                sx={{
                  marginLeft: "8px",
                  color: "text.primary",
                  fontWeight: "fontWeightRegular",
                }}
                icon="solar:alt-arrow-down-linear"
              />
            </LoadingButton>
            <IconButton onClick={onClose} size="small">
              <Iconify icon="solar:close-linear" />
            </IconButton>
          </Stack>
        </Box>
        <Box
          style={{
            overflowX: "auto",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              tableLayout: "auto",
            }}
          >
            <thead>
              <tr>
                {datasetCols?.map((col, index) => (
                  <th
                    key={index}
                    style={{
                      minWidth: "33vw",
                      width: isTwoDataset ? "48vw" : "unset",
                      borderTop: "1px solid",
                      borderBottom: "1px solid",
                      borderRight:
                        index !== datasetCols?.length - 1
                          ? "1px solid"
                          : "none",
                      borderColor: theme.palette.divider,
                    }}
                  >
                    <Box
                      key={index}
                      sx={{
                        minWidth: "33vw",
                        width: isTwoDataset ? "48vw" : "unset",
                        display: "flex",
                        gap: "12px",
                        alignItems: "center",
                        // borderTop: "1px solid",
                        // borderBottom: "1px solid",
                        // borderRight:
                        //   index !== datasetCols?.length - 1
                        //     ? "1px solid"
                        //     : "none",
                        // borderColor: "divider",
                        padding: "16px",
                        position: "sticky",
                        top: 0,
                        zIndex: 15,
                        backgroundColor: "background.paper",
                      }}
                    >
                      <Box
                        sx={{
                          height: "24px",
                          width: "24px",
                          borderRadius: "4px",
                          backgroundColor:
                            getUniqueColorPalette(index).tagBackground,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <Typography
                          color={getUniqueColorPalette(index).tagForeground}
                          variant="s2"
                          fontWeight={"fontWeightSemiBold"}
                        >
                          {String.fromCharCode(65 + index)}
                        </Typography>
                      </Box>
                      <Typography
                        variant="s1"
                        fontWeight={"fontWeightSemiBold"}
                        color="text.primary"
                      >
                        {" "}
                        {col?.name}
                      </Typography>
                    </Box>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr
                ref={resizableRowRef}
                style={{
                  height: `${height}px`,
                  overflow: "hidden",
                  position: "relative",
                }}
              >
                {datasetCols?.map((col, index) => (
                  <td
                    key={index}
                    style={{
                      verticalAlign: "top",
                      height: `${height}px`,
                      overflow: "hidden",
                      borderTop: "1px solid",
                      borderBottom: "1px solid",
                      borderRight:
                        index !== datasetCols?.length - 1
                          ? "1px solid"
                          : "none",
                      borderColor: theme.palette.divider,
                    }}
                  >
                    <Box
                      component={"div"}
                      key={index}
                      className="ag-theme-alpine"
                      sx={{
                        minWidth: "33vw",
                        width: isTwoDataset ? "48vw" : "unset",
                        overflowY: "auto",
                        height: `100%`,
                        pt: "16px",
                        px: "8px",
                        pb: "12px",
                      }}
                    >
                      <AgGridReact
                        theme={agTheme}
                        suppressContextMenu={true}
                        columnDefs={TabColumnDefs}
                        defaultColDef={defaultColDef}
                        rowData={evalsData?.[col?.datasetId] ?? []}
                        domLayout="normal"
                        suppressCellFocus={true}
                        suppressRowClickSelection={true}
                        suppressRowDrag={true}
                        onCellClicked={(event) => {
                          if (event.colDef.headerName === "Description") {
                            setOpenEvalDetailDrawer(true);
                            setSelectedEvalDetail({
                              ...row?.[event?.data?.id],
                              headerName: `${col?.name}-${event?.data?.group?.name}`,
                              evalName: event?.data?.group?.name,
                              evalId: event?.data?.id,
                              rowId: row?.[event?.data?.id]?.cellRowId,
                              sourceId: event?.data?.sourceId,
                            });
                          }
                        }}
                        noRowsOverlayComponent={CustomNoRowsOverlay}
                      />
                    </Box>
                  </td>
                ))}
                <td
                  style={{ padding: 0, margin: 0 }}
                  // colSpan={datasetCols?.length}
                >
                  <div
                    style={{
                      position: "absolute",
                      bottom: "-10px",
                      left: "8px",
                      right: "0",
                      zIndex: 1,
                    }}
                  >
                    <Box
                      className="resizer"
                      sx={{
                        position: "sticky",
                        bottom: "10px",
                        left: "0",
                        borderRadius: "50%",
                        zIndex: 10,
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "center",
                        border: "1px solid",
                        borderColor: "divider",
                        backgroundColor: "background.paper",
                        height: "20px",
                        width: "20px",
                        cursor: isDragging ? "grabbing" : "grab",
                      }}
                      component={"img"}
                      onMouseDown={handleMouseDown}
                      src="/assets/icons/ic_dragger.svg"
                    />
                  </div>
                </td>
              </tr>

              <tr>
                {datasetCols.map((col, index) => (
                  // will contain ind cols
                  <td
                    key={index}
                    style={{
                      verticalAlign: "top",
                      height: "100%",
                      borderTop: "1px solid",
                      borderRight:
                        index !== datasetCols?.length - 1
                          ? "1px solid"
                          : "none",
                      borderColor: theme.palette.divider,
                    }}
                  >
                    <Stack
                      sx={{
                        minWidth: "33vw",
                        width: isTwoDataset ? "48vw" : "unset",
                        // borderRight: "1px solid",
                        // borderColor: "divider",
                        // marginTop: "12px"
                        py: "12px",
                        px: "8px",
                        rowGap: "8px",
                      }}
                      key={index}
                    >
                      <Typography
                        variant="s1"
                        fontWeight={"fontWeightMedium"}
                        color={"text.primary"}
                        sx={{
                          mb: "8px",
                        }}
                      >
                        Dataset Details
                      </Typography>
                      {indColsHash?.[col?.datasetId]?.map((eachCol) => {
                        const value = row?.[eachCol?.id];
                        return eachCol?.dataType === "audio" ? (
                          <AudioDatapointCard
                            key={eachCol?.id}
                            value={value}
                            column={{
                              dataType: eachCol?.dataType,
                              headerName: eachCol?.group?.name,
                            }}
                          />
                        ) : eachCol?.dataType === "images" ? (
                          <ImagesDatapointCard
                            key={eachCol?.id}
                            value={value}
                            column={{
                              dataType: eachCol?.dataType,
                              headerName: eachCol?.group?.name,
                            }}
                          />
                        ) : (
                          <DatapointCard
                            key={eachCol?.id}
                            showDiff={value?.cellDiffValue}
                            value={value}
                            column={{
                              dataType: eachCol?.dataType,
                              headerName: eachCol?.group?.name,
                            }}
                            onDiffClick={(tabValue) =>
                              handleToggleDiffForSingleCol(
                                eachCol?.id,
                                tabValue,
                              )
                            }
                            activeTab={
                              !activeTab && indColsDifTracker[eachCol?.id]
                                ? "difference"
                                : indColsDifTracker[eachCol?.id] === false
                                  ? activeTab
                                  : ""
                            }
                          />
                        );
                      })}
                    </Stack>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </Box>
      </Box>
      <ViewDetailsModal
        evalDetail={selectedEvalDetail}
        open={openEvalDetailDrawer}
        onClose={() => setOpenEvalDetailDrawer(false)}
        clearEvalDetail={() => setSelectedEvalDetail(null)}
        handleOpenFeedbackForm={handleOpenFeedbackForm}
      />
      <CompareDatasetEvalsAddFeedbackForm
        evalDetail={selectedEvalDetail}
        open={openfeedbackForm}
        onClose={handleCLoseFeedbackForm}
      />
    </>
  );
}

CompareDatasetDetailContent.propTypes = {
  onClose: PropTypes.func,
  row: PropTypes.object,
  columnConfig: PropTypes.array,
  showDiff: PropTypes.bool,
  setShowDiff: PropTypes.func,
  handleToggleDiff: PropTypes.func,
  nextRowId: PropTypes.string,
  prevRowId: PropTypes.string,
  handleFetchNextRow: PropTypes.func,
  handleFetchPrevRow: PropTypes.func,
  isPending: PropTypes.bool,
};
