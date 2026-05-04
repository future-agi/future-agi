import {
  Box,
  Chip,
  IconButton,
  Stack,
  Switch,
  Typography,
  useTheme,
  LinearProgress,
  Skeleton,
  Button,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import ExperimentDescriptionCell from "./ExperimentDescriptionCell";
import DatapointCard from "src/sections/common/DatapointCard";
import AudioDatapointCard from "src/components/custom-audio/AudioDatapointCard";
import ViewDetailsModal from "./ViewDetailsModal";
import { LoadingButton } from "@mui/lab";
import { getUniqueColorPalette } from "src/utils/utils";
import {
  getLabel,
  getStatusColor,
} from "src/sections/develop-detail/DataTab/common";
import { ShowComponent } from "src/components/show/ShowComponent";
import SvgColor from "src/components/svg-color/svg-color";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import ImageDatapointCard from "src/sections/common/ImageDatapointCard";
import AgentFlowRenderer from "./AgentFlowRenderer";
import { useAddEvaluationFeebackStore } from "src/sections/develop-detail/states";
import AddEvaluationFeeback from "src/sections/develop-detail/DataTab/AddEvaluationFeeback/AddEvaluationFeeback";

// Skeleton loader for loading states
const SkeletonLoader = () => (
  <Skeleton
    variant="rounded"
    width={80}
    height={24}
    sx={{ borderRadius: 1, margin: "5px" }}
  />
);

// StatusCellRenderer that properly handles different data types like the dataset detail drawer
export const StatusCellRenderer = (props) => {
  const theme = useTheme();
  const { value } = props;
  let cellValue = value?.cellValue;
  const status = value?.status;
  const type = props?.data?.dataType;

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

  if (type === OutputTypes.NUMERIC) {
    return <NumericCell value={cellValue} sx={{ padding: "0 12px" }} />;
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
  value: PropTypes.shape({
    cellValue: PropTypes.any,
    status: PropTypes.string,
    dataType: PropTypes.string,
  }),
  data: PropTypes.shape({
    dataType: PropTypes.string,
  }),
};

const EXPERIMENT_DRAWER_THEME_PARAMS = {
  headerColumnBorder: { width: "0px" },
  headerBackgroundColor: "background.paper",
  fontSize: "14px",
};

export default function ExperimentDetailDrawerContent({
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
  handleRefetchRowData,
  refreshGrid,
  showDiffModeButton = false,
}) {
  // State management
  const [height, setHeight] = useState(250);
  const [indColsDifTracker, setIndColsDifTracker] = useState({});
  const [activeTab, setActiveTab] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [openDetailRow, setOpenDetailRow] = useState(null);
  const [showDescription, setShowDescription] = useState(false);
  const theme = useTheme();
  const agTheme = useAgThemeWith(EXPERIMENT_DRAWER_THEME_PARAMS);
  const resizableRowRef = useRef(null);
  const { setAddEvaluationFeeback } = useAddEvaluationFeebackStore();

  // Column grouping logic - separates individual columns from dataset columns
  const { individualCols: _individualCols, datasetCols } = useMemo(() => {
    const grouping = {};
    const individualCols = [];
    const datasetCols = [];

    // Group columns by their group ID
    for (const item of columnConfig) {
      if (!grouping[item?.group?.id]) {
        grouping[item?.group?.id] = [];
      }
      grouping[item?.group?.id].push(item);
    }
    // Separate individual columns from dataset columns
    for (const [_, value] of Object.entries(grouping)) {
      if (value.length === 1) {
        const col = value?.[0];
        const isBaseColumn = col?.is_base_column || col?.isBaseColumn;
        const originType =
          col?.origin_type || col?.originType || col?.group?.origin;
        const isExperimentType =
          originType === "Experiment" || originType === "experiment";

        // Add to datasetCols if base column or experiment type, otherwise to individualCols
        if (isBaseColumn || isExperimentType) {
          datasetCols.push(col);
        } else {
          individualCols.push(col);
        }
      } else if (value.length > 1 && value[0]?.group?.origin === "Experiment") {
        // For agent columns, push only the final output
        const isAgentGroup = value[0]?.is_agent || value[0]?.isAgent;
        if (isAgentGroup) {
          const finalCol = value.find((col) => col?.is_final || col?.isFinal);
          if (finalCol) {
            // Attach all grouped columns as extra data for the renderer
            datasetCols.push({
              ...finalCol,
              agentGroupedColumns: value,
            });
          }
        } else {
          // Sort to match grid order: alphabetical by base name, -reason after base
          const firstCol = value[0];
          const rest = value.slice(1);
          // eslint-disable-next-line no-shadow
          rest.sort((a, b) => {
            const nameA = a?.name || "";
            const nameB = b?.name || "";
            const baseA = nameA.replace(/-reason.*/, "");
            const baseB = nameB.replace(/-reason.*/, "");
            if (baseA !== baseB) return baseA.localeCompare(baseB);
            if (nameA.endsWith("-reason") && !nameB.endsWith("-reason"))
              return 1;
            if (!nameA.endsWith("-reason") && nameB.endsWith("-reason"))
              return -1;
            return 0;
          });

          datasetCols.push(firstCol, ...rest);
        }
      }
    }
    return { individualCols, datasetCols };
  }, [columnConfig]);

  // Evaluation table column definitions

  const TabColumnDefs = useMemo(() => {
    const tabs = [
      {
        headerName: "Evaluation Metrics",
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
        valueGetter: (params) => row?.[params?.data?.id],
      },
    ];

    if (showDescription) {
      tabs.push({
        headerName: "Description",
        field: "id",
        flex: 1,
        valueGetter: () => "View Description",
        cellRenderer: ExperimentDescriptionCell,
        resizable: false,
        minWidth: 100,
      });
    }

    return tabs;
  }, [showDescription]);
  // Custom overlay for empty evaluation data
  const CustomNoRowsOverlay = () => (
    <Box
      sx={{
        padding: 2,
        textAlign: "center",
        color: "text.primary",
        fontSize: 14,
        fontWeight: 400,
      }}
    >
      No Evaluations Applied
    </Box>
  );

  // Default column configuration for AG Grid
  const defaultColDef = {
    lockVisible: true,
    sortable: false,
    filter: false,
    resizable: true,
    suppressHeaderMenuButton: true,
    suppressHeaderContextMenu: true,
  };

  // Filter and group evaluation data by dataset ID, excluding summary/reason columns
  const evalsData = useMemo(() => {
    const datasetEvaluations = {};
    const evaluations = columnConfig?.filter((i) => {
      // Only include evaluation columns, but exclude summary/reason columns
      return i?.originType === "evaluation" && !i?.name?.includes("-reason");
    });

    if (evaluations && evaluations?.length > 0) {
      for (const item of evaluations) {
        if (datasetEvaluations[item?.datasetId]) {
          datasetEvaluations[item?.datasetId].push(item);
        } else {
          datasetEvaluations[item?.datasetId] = [item];
        }
      }
    }
    // Sort each dataset array by name once
    for (const key in datasetEvaluations) {
      datasetEvaluations[key].sort((a, b) => a?.name?.localeCompare(b?.name));
    }
    return datasetEvaluations;
  }, [columnConfig]);
  // Effect to handle diff tracking for dataset columns
  useEffect(() => {
    if (isPending) return;
    if (datasetCols?.length > 0) {
      const diffTracker = {};
      for (const datasetCol of datasetCols) {
        if (row?.[datasetCol?.id]?.cellDiffValue) {
          diffTracker[datasetCol?.id] = showDiff;
        }
      }

      setIndColsDifTracker(diffTracker);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetCols, row, isPending]);

  // Handle diff toggle for individual columns
  const handleToggleDiffForSingleCol = (datasetColId, tabValue) => {
    const copy = { ...indColsDifTracker };
    if (datasetColId in copy) {
      copy[datasetColId] = Boolean(tabValue === "difference");
    }

    if (Object.keys(copy).length < 1) return;

    const isAllDiffOn = Object.values(copy).every((val) => val === true);
    setShowDiff(isAllDiffOn);
    setIndColsDifTracker(copy);
    setActiveTab(isAllDiffOn ? "difference" : "");
  };

  // Effect to sync diff state with individual column trackers
  useEffect(() => {
    const copy = { ...indColsDifTracker };
    if (Object.keys(copy).length < 1) return;

    const isAllDiffOn = Object.values(copy).every((val) => val === true);
    setShowDiff(isAllDiffOn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indColsDifTracker]);

  // Handle mouse events for resizable table row
  const handleMouseDown = (e) => {
    e.preventDefault();
    setTimeout(() => {
      setIsDragging(true);
    }, 0);
  };

  // Effect to handle mouse events for resizing
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

  const isTwoDataset = datasetCols?.length === 2;

  // Show loading state when data is being fetched
  if (isPending && !row) {
    return (
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
          <Skeleton variant="text" width={200} height={32} />
          <Stack direction={"row"} gap={"12px"} alignItems={"center"}>
            <Skeleton variant="rectangular" width={100} height={32} />
            <Skeleton variant="rectangular" width={80} height={32} />
            <Skeleton variant="rectangular" width={80} height={32} />
            <Skeleton variant="circular" width={32} height={32} />
          </Stack>
        </Box>
        <LinearProgress />
        <Box sx={{ padding: 4, textAlign: "center" }}>
          <Typography variant="body2" color="text.secondary">
            Loading experiment data...
          </Typography>
        </Box>
      </Box>
    );
  }

  const renderDatapoint = (col, row) => {
    if (col.dataType === "audio") {
      return <AudioDatapointCard value={row?.[col.id]} column={col} />;
    }

    if (col?.dataType === "image") {
      return (
        <ImageDatapointCard
          value={row?.[col.id]}
          column={{ ...col, headerName: col?.name, status: col?.status }}
        />
      );
    }

    // Render agent flow for agent columns
    if (col?.isAgent) {
      // Use agentGroupedColumns if available, otherwise find them
      const groupedAgentColumns =
        col?.agentGroupedColumns ||
        datasetCols.filter(
          (c) => c?.group?.id === col?.group?.id && c?.isAgent,
        );

      if (groupedAgentColumns.length > 0) {
        // Extract values from each grouped column in order
        // eslint-disable-next-line no-shadow
        const outputs = groupedAgentColumns.map((agentCol) => {
          const cellData = row?.[agentCol?.id] || {};
          return {
            ...cellData,
            columnName: agentCol?.name,
            isFinal: agentCol?.isFinal,
          };
        });

        return (
          <AgentFlowRenderer
            outputs={outputs}
            showDiff={
              row?.[col?.id]?.cellDiffValue ||
              Array.isArray(row?.[col?.id]?.cellValue)
            }
            onDiffClick={(tabValue) =>
              handleToggleDiffForSingleCol(col?.id, tabValue)
            }
            activeTab={
              !activeTab && indColsDifTracker[col?.id]
                ? "difference"
                : indColsDifTracker[col?.id] === false
                  ? activeTab
                  : ""
            }
            indColsDifTracker={indColsDifTracker}
            colId={col?.id}
          />
        );
      }
    }

    // insert cellDiffValue key if diffTracker is true
    // else for raw & markdown if it is still array convert it to string by taking default and added
    const value = {
      ...(row?.[col.id] || {}),
      ...(indColsDifTracker?.[col?.id] &&
      Array.isArray(row?.[col.id]?.cellValue)
        ? { cellDiffValue: row?.[col.id]?.cellValue }
        : {
            cellValue: Array.isArray(row?.[col.id]?.cellValue)
              ? row?.[col.id]?.cellValue
                  .filter(
                    (item) =>
                      item.status === "default" || item.status === "added",
                  )
                  .map((item) => item.text)
                  .join(" ") // if array but not diff tab filter normal text from the array
              : row?.[col?.id]?.cellValue,
          }),
    };

    return (
      <DatapointCard
        value={value}
        showDiff={
          row?.[col?.id]?.cellDiffValue ||
          Array.isArray(row?.[col?.id]?.cellValue)
        }
        column={{
          dataType: col?.dataType,
          headerName: col?.name,
        }}
        onDiffClick={(tabValue) =>
          handleToggleDiffForSingleCol(col?.id, tabValue)
        }
        activeTab={
          !activeTab && indColsDifTracker[col?.id]
            ? "difference"
            : indColsDifTracker[col?.id] === false
              ? activeTab
              : ""
        }
        indColsDifTracker={indColsDifTracker}
      />
    );
  };

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
          <Typography variant="m3" fontWeight={700} color="text.primary">
            Experiments
          </Typography>
          <Stack direction={"row"} gap={"12px"} alignItems={"center"}>
            <ShowComponent condition={showDiff}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <SvgColor
                  src="/icons/datasets/diff_pencil.svg"
                  sx={{ height: "16px", width: "16px", color: "red.500" }}
                />
                <Typography
                  variant="s3"
                  color="red.500"
                  fontWeight={"fontWeightRegular"}
                >
                  Missing Text
                </Typography>
              </Box>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <SvgColor
                  src="/icons/datasets/diff_pencil.svg"
                  sx={{ height: "16px", width: "16px", color: "green.500" }}
                />
                <Typography
                  variant="s3"
                  color="green.500"
                  fontWeight={"fontWeightRegular"}
                >
                  New Text
                </Typography>
              </Box>
            </ShowComponent>
            <Button
              variant="outlined"
              size="small"
              onClick={() => setShowDescription(!showDescription)}
            >
              {"Show Descriptions"}
              <Switch
                size="small"
                sx={{
                  "& .Mui-checked+.MuiSwitch-track": {
                    backgroundColor: (theme) =>
                      `${theme.palette.primary[500]} !important`,
                  },
                }}
                checked={showDescription}
              />
            </Button>
            {/* <Box sx={{ display: "flex", alignItems: "center" }}>
              <Switch
                size="small"
                color="success"
                checked={showDiff}
                disabled={isPending}
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
                title="Shows the difference between original column vs configured columns of the dataset "
                placement="bottom"
                arrow
              >
                <Iconify
                  width={16}
                  icon="material-symbols:info-outline-rounded"
                  sx={{ color: "text.disabled", marginLeft: 0.5 }}
                />
              </CustomTooltip>
            </Box> */}
            <ShowComponent condition={showDiffModeButton}>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
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
                padding={"8px 12px"}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  color: "black.1000",
                  cursor: "pointer",
                }}
              >
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightMedium"}
                  sx={{
                    color: "text.primary",
                  }}
                >
                  Show Diff
                </Typography>
                <Switch
                  size="small"
                  sx={{
                    "& .Mui-checked+.MuiSwitch-track": {
                      backgroundColor: (theme) =>
                        `${theme.palette.primary[500]} !important`,
                    },
                  }}
                  checked={showDiff}
                />
              </Button>
            </ShowComponent>

            <LoadingButton
              loading={isPending}
              onClick={handleFetchPrevRow}
              disabled={Boolean(prevRowId || isPending)}
              size="small"
              sx={{
                border: "1px solid",
                borderColor: "action.hover",
                borderRadius: "4px",
                py: "2px",
                px: "16px",
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
                icon="material-symbols:expand-less-rounded"
              />
            </LoadingButton>
            <LoadingButton
              loading={isPending}
              onClick={handleFetchNextRow}
              disabled={Boolean(nextRowId || isPending)}
              size="small"
              sx={{
                border: "1px solid",
                borderColor: "action.hover",
                borderRadius: "4px",
                py: "2px",
                px: "16px",
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
                icon="material-symbols:expand-more-rounded"
              />
            </LoadingButton>
            <IconButton onClick={onClose} size="small">
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Stack>
        </Box>

        {/* Show loading indicator when fetching data */}
        {isPending && <LinearProgress sx={{ mb: 1 }} />}

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
                        padding: "16px",
                        position: "sticky",
                        top: 0,
                        zIndex: 15,
                        backgroundColor: "background.paper",
                      }}
                    >
                      {col?.group?.origin !== "Dataset" && (
                        <Box
                          sx={{
                            height: "24px",
                            width: "24px",
                            borderRadius: "4px",
                            backgroundColor:
                              index > 0
                                ? getUniqueColorPalette(index).tagBackground
                                : "unset",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                          }}
                        >
                          <Typography
                            color={
                              index > 0
                                ? getUniqueColorPalette(index).tagForeground
                                : "unset"
                            }
                            variant="s2"
                            fontWeight={"fontWeightSemiBold"}
                          >
                            {String.fromCharCode(
                              65 + (index > 0 ? index - 1 : 0),
                            )}
                          </Typography>
                        </Box>
                      )}
                      <Typography
                        variant="s1"
                        fontWeight={"fontWeightSemiBold"}
                        color="text.primary"
                      >
                        {" "}
                        {col?.group?.name}
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
                        suppressRowDrag={true}
                        noRowsOverlayComponent={CustomNoRowsOverlay}
                        onCellClicked={(event) => {
                          if (event.colDef.headerName === "Description") {
                            const evalData = row?.[event?.data?.id];
                            if (evalData) {
                              setOpenDetailRow({
                                ...event.data,
                                ...evalData,
                                headerName: `${col?.name}-${event?.data?.group?.name}`,
                                evalName: event?.data?.group?.name,
                                evalId: event?.data?.id,
                                rowId: evalData?.cellRowId,
                                sourceId: event?.data?.sourceId,
                              });
                            }
                          }
                        }}
                      />
                    </Box>
                  </td>
                ))}
                <td style={{ padding: 0, margin: 0 }}>
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
                        padding: "8px",
                        rowGap: "8px",
                      }}
                      key={index}
                    >
                      <ShowComponent condition={!col?.isAgent}>
                        <Typography
                          variant="s1"
                          color={"text.primary"}
                          fontWeight={"fontWeightMedium"}
                        >
                          {col?.group?.origin === "Dataset"
                            ? "Dataset details"
                            : "Experiment details"}
                        </Typography>
                      </ShowComponent>

                      {renderDatapoint(col, row)}
                      {/* {individualCols.map((eachCol) => {
                        const value = row?.[eachCol?.id];

                        return eachCol?.dataType === "audio" ? (
                          <AudioDatapointCard
                            key={eachCol?.id}
                            value={value}
                            column={{
                              dataType: eachCol?.dataType,
                              headerName: eachCol?.name,
                            }}
                          />
                        ) : (
                          <DatapointCard
                            key={eachCol?.id}
                            value={value}
                            showDiff={value?.cellDiffValue}
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
                      })} */}
                    </Stack>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </Box>
      </Box>
      {openDetailRow && (
        <ViewDetailsModal
          open={true}
          onClose={() => setOpenDetailRow(null)}
          data={openDetailRow}
          refreshGrid={refreshGrid}
          handleRefetchRowData={handleRefetchRowData}
          isRefetching={isPending}
          onAddFeedbackClick={(evalData) => {
            if (!evalData?.group?.id || !evalData?.id) return;
            setAddEvaluationFeeback({
              ...evalData,
              userEvalMetricId: evalData?.group?.id,
              sourceId: evalData?.id,
              rowData: { rowId: row?.rowId },
            });
            setOpenDetailRow(null);
          }}
        />
      )}

      <AddEvaluationFeeback module="experiment" onRefreshGrid={refreshGrid} />
    </>
  );
}

ExperimentDetailDrawerContent.propTypes = {
  onClose: PropTypes.func,
  row: PropTypes.object,
  columnConfig: PropTypes.array,
  showDiff: PropTypes.bool,
  setShowDiff: PropTypes.func,
  handleToggleDiff: PropTypes.func,
  nextRowId: PropTypes.bool,
  prevRowId: PropTypes.bool,
  handleFetchNextRow: PropTypes.func,
  handleFetchPrevRow: PropTypes.func,
  isPending: PropTypes.bool,
  handleRefetchRowData: PropTypes.func,
  refreshGrid: PropTypes.func,
  diffMode: PropTypes.bool,
  showDiffModeButton: PropTypes.bool,
};
