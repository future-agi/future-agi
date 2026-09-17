import { Alert, Box, Button, Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import ExperimentDetailDrawerContent from "./ExperimentDetailDrawerContent";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { enqueueSnackbar } from "notistack";
import { readLegacyExperimentRow } from "./legacy_experiment_row_read";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

// const ExperimentDetailSection = ({ col, individualCols, row, columnConfigs, index, refreshGrid }) => {
//   const evalsData = useMemo(() => {
//     return columnConfigs.filter((i) => i.originType == "evaluation");
//   }, []);

//   const safeIndex = index % 10;
//   const { bg, text } = getColorByIndex(safeIndex);
//   const [openDetailRow, setOpenDetailRow] = useState(null);

//   const combinedEvalsData = evalsData.map((evalItem) => {
//     const rowData = row?.[evalItem.id];
//     if (rowData) {
//       return {
//         ...evalItem,
//         cellValue: rowData.cellValue,
//         status: rowData.status,
//         metadata: rowData?.metadata,
//       };
//     }
//     return evalItem;
//   });

//   const filteredEvalsData = useMemo(() => {
//     return combinedEvalsData.filter(
//       (evalItem) => evalItem?.datasetId === col?.datasetId
//     );
//   }, [combinedEvalsData, col?.datasetId]);

// const TabColumnDefs = (setOpenDetailRow) => [
//   {
//     headerName: "Evaluation Metrics",
//     field: "group.name",
//     flex: 1,
//   },
//   {
//     headerName: "Score",
//     field: "status",
//     flex: 1,
//     cellRenderer: StatusCellRenderer,
//   },
//   {
//     headerName: "Description",
//     field: "detail",
//     flex: 1,
//     cellRenderer: ViewDetailCellRenderer,
//     cellRendererParams: {
//       setOpenDetailRow,
//     },
//   },
// ];

//   const CustomNoRowsOverlay = () => {
//     return (
//       <Box
//         sx={{
//           padding: 2,
//           textAlign: "center",
//           color: "text.primary",
//           fontSize: 14,
//           fontWeight:400
//         }}
//       >
//         No Evalutations Applied
//       </Box>
//     );
//   };

//   const defaultColDef = {
//     sortable: true,
//     filter: false,
//     resizable: true,
//     suppressMenu: true,
//   };

//   const [isDragging, setIsDragging] = useState(false);
//   const boxRef = useRef(null);
//   const [selectedDetailId, setSelectedDetailId] = useState(null);

//   const handleMouseDown = () => {
//     setIsDragging(true);
//   };

//   const handleMouseUp = () => {
//     setIsDragging(false);
//   };

//   const handleMouseMove = (e) => {
//     if (isDragging) {
//       const rect = boxRef.current.getBoundingClientRect();
//       let newHeight = e.clientY - rect.y;
//       newHeight = Math.max(0, newHeight);
//       newHeight = Math.round(newHeight);
//       setHeight(newHeight);
//     }
//   };

//   useEffect(() => {
//     if (isDragging) {
//       window.addEventListener("mousemove", handleMouseMove);
//       window.addEventListener("mouseup", handleMouseUp);
//     } else {
//       window.removeEventListener("mousemove", handleMouseMove);
//       window.removeEventListener("mouseup", handleMouseUp);
//     }

//     return () => {
//       window.removeEventListener("mousemove", handleMouseMove);
//       window.removeEventListener("mouseup", handleMouseUp);
//     };
//   }, [isDragging]);

//   return (
//     <Box
//       sx={{
//         minWidth: "35vw",
//         display: "flex",
//         flexDirection: "column",
//         flex: 1,
//         gap: 1.5,
//       }}
//     >
//       <Box
//         sx={{
//           display: "flex",
//           gap: "12px",
//           alignItems: "center",
//           borderBottom: commonBorder.border,
//           borderColor: commonBorder.borderColor,
//           padding: 1.5,
//         }}
//       >
//         <Box
//           sx={{
//             width: "24px",
//             height: "25px",
//             borderRadius: "4px",
//             backgroundColor: bg,
//             display: "flex",
//             alignItems: "center",
//             justifyContent: "center",
//             fontSize: 12,
//             fontWeight: 600,
//             color: text,
//           }}
//         >
//           {String.fromCharCode(65 + index)}
//           {/* E{index} */}
//         </Box>
//         <Typography fontWeight={600} fontSize={14} color="text.primary">
//           {col?.group?.name}
//         </Typography>
//       </Box>
//       <Box
//         sx={{
//           display: "flex",
//           flexDirection: "column",
//           gap: 2,
//         }}
//       >
//         <Box
//           ref={boxRef}
//           sx={{
//             minHeight: "220px",
//             maxHeight: "300px",
//             width: "100%",
//             paddingX: 1.2,
//             paddingBottom: 2,
//             borderBottom: commonBorder.border,
//             borderColor: commonBorder.borderColor,
//           }}
//         >
//           <div
//             className="ag-theme-alpine"
//             style={{
//               height: `${height - 40}px`,
//               overflow: "auto",
//               paddingBottom: "12px",
//             }}
//           >
//             <AgGridReact
//               theme={agTheme.withParams({
//                 headerColumnBorder: {
//                   width: "0px",
//                 },
//                 headerBackgroundColor: "background.paper",
//                 fontSize:"14px"
//               })}
//               columnDefs={TabColumnDefs(setOpenDetailRow)}
//               defaultColDef={defaultColDef}
//               rowData={filteredEvalsData}
//               domLayout="autoHeight"
//               suppressRowDrag={true}
//               noRowsOverlayComponent={CustomNoRowsOverlay}
//             />
//           </div>
//           <Box
//             component={"button"}
//             sx={{
//               position: "sticky",
//               bottom: "-20px",
//               left: "20px",
//               borderRadius: "50%",
//               zIndex: 10,
//               display: index === 0 ? "flex" : "none",
//               justifyContent: "center",
//               alignItems: "center",
//               border: "1px solid",
//               borderColor: "divider",
//               backgroundColor: isDragging ? "background.neutral" : "background.paper",
//               cursor: isDragging ? "grabbing" : "grab",
//               height: "20px",
//               width: "20px",
//             }}
//             onMouseDown={handleMouseDown}
//           >
//             <Box
//               sx={{ userSelect: "none" }}
//               component={"img"}
//               src="/assets/icons/ic_dragger.svg"
//             />
//             {/* <Iconify icon="oui:grab" width={16} sx={{ color: "text.disabled" }} /> */}
//           </Box>
//         </Box>
//         <Box
//           sx={{
//             display: "flex",
//             flexDirection: "column",
//             gap: 2,
//             paddingX: 1.2,
//           }}
//         >
//         {col.dataType === "audio" ? (
//           <AudioDatapointCard value={row?.[col.id]} column={col} />
//         ) : (
//           <DatapointCard
//             value={row?.[col.id]}
//             column={{
//               dataType: col.dataType,
//               headerName: col.name,
//             }}
//           />
//         )}
//         {individualCols?.map((eachCol) => {
//           const value = row?.[eachCol.id];

//           return eachCol.dataType === "audio" ? (
//             <AudioDatapointCard
//               key={eachCol.id}
//               value={value}
//               column={{
//                 dataType: eachCol.dataType,
//                 headerName: eachCol.name,
//               }}
//             />
//           ) : (
//             <DatapointCard
//               key={eachCol.id}
//               value={value}
//               column={{
//                 dataType: eachCol.dataType,
//                 headerName: eachCol.name,
//               }}
//             />
// );
//         })}
//         </Box>
//         </Box>
//       {openDetailRow && (
//         <ViewDetailsModal
//           open={true}
//           onClose={() => setOpenDetailRow(null)}
//           data={openDetailRow}
//           refreshGrid={refreshGrid}
//         />
//       )}
//     </Box>
//   );
// };

// ExperimentDetailSection.propTypes = {
//   col: PropTypes.object,
//   individualCols: PropTypes.array,
//   row: PropTypes.object,
//   columnConfigs: PropTypes.array,
//   index: PropTypes.number,
//   refreshGrid: PropTypes.func,
// };

// const ExperimentDetailDrawerChild = ({ onClose, row, columnConfig, allRows,
//   setExpandRow, refreshGrid }) => {

//   if (!row) return null;
//   const theme = useTheme();

//   const handleNextDatapoint = () => {
//     const currentIndex = row.index;
//     if (currentIndex < allRows.length - 1) {
//       setExpandRow({ ...allRows[currentIndex + 1], index: currentIndex + 1 });
//     }
//   };

//   const handlePrevDatapoint = () => {
//     const currentIndex = row.index;
//     if (currentIndex > 0) {
//       setExpandRow({ ...allRows[currentIndex - 1], index: currentIndex - 1 });
//     }
//   };

//   const { individualCols, datasetCols } = useMemo(() => {
//     const grouping = {};
//     const individualCols = [];
//     const datasetCols = [];

//     for (const item of columnConfig) {
//       if (!grouping[item.group.id]) {
//         grouping[item.group.id] = [];
//       }
//       grouping[item.group.id].push(item);
//     }

//     for (const [key, value] of Object.entries(grouping)) {
//       if (value.length === 1) {
//         individualCols.push(value[0]);
//       } else if (value.length > 1 && value[0]?.group?.origin === "Dataset") {
//         datasetCols.push(...value);
//       }
//     }

//     return { individualCols, datasetCols };
//   }, [columnConfig]);

//   const [height, setHeight] = useState(250);

//   return (
//     <Box
//     sx={{
//       width: "100vw",
//       display: "flex",
//       flexDirection: "column",
//       height: "100vh",
//       position: "relative",
//     }}
//   >
//     <Box
//       sx={{
//         width: "100vw",
//         padding: "16px",
//         height: "100%",
//         display: "flex",
//         gap: 1,
//         flexDirection: "column",
//         overflow: "auto",
//       }}
//     >
//       <Typography fontWeight={500} fontSize={16} color="text.primary">
//         Experiments
//       </Typography>
//         <Box
//           sx={{
//             display: "flex",
//           }}
//         >
//           <LoadingButton
//             aria-label="prev-exp"
//             type="button"
//             variant="outlined"
//             onClick={handlePrevDatapoint}
//             disabled={row.index === 0}
//             size="small"
//             sx={{
//               marginRight: theme.spacing(1.25),
//               paddingLeft: theme.spacing(1.875),
//               borderColor: "divider",
//               color: "text.disabled",
//             }}
//           >
//             Prev
//             <Iconify
//               sx={{ marginLeft: theme.spacing(1.25) }}
//               icon="material-symbols:expand-less-rounded"
//             />
//           </LoadingButton>
//           <LoadingButton
//             aria-label="next-exp"
//             type="button"
//             variant="outlined"
//             onClick={handleNextDatapoint}
//             disabled={row.index >= allRows.length - 1}
//             size="small"
//             sx={{
//               marginRight: theme.spacing(1.25),
//               paddingLeft: theme.spacing(1.875),
//               borderColor: "divider",
//               color: "text.disabled",
//             }}
//           >
//             Next
//             <Iconify
//               sx={{ marginLeft: theme.spacing(1.25) }}
//               icon="material-symbols:expand-more-rounded"
//             />
//           </LoadingButton>
//           <IconButton aria-label="close-experiment-detail" onClick={onClose} size="small">
//             <Iconify icon="mingcute:close-line" color="text.primary" />
//           </IconButton>
//         </Box>
//     </Box>

//     {/* Scrollbar wrapper */}
//     <Box
//       sx={{
//         position: "sticky",
//         bottom: 0,
//         zIndex: 1000,
//         overflowX: "auto",
//         backgroundColor: "background.paper",
//       }}
//     >
//       {/* Actual scrollable content */}
//       <Box
//         sx={{
//           display: "flex",
//           borderRight: commonBorder.border,
//           borderLeft: commonBorder.border,
//           borderColor: commonBorder.borderColor,
//         }}
//       >
//         {datasetCols?.map((col, index) => (
//           <Box
//             key={col.id}
//             sx={{
//               borderLeft: commonBorder.border,
//               borderTop: commonBorder.border,
//               borderBottom: commonBorder.border,
//               borderColor: commonBorder.borderColor,
//               paddingBottom: theme.spacing(1),
//               width:"38%"
//             }}
//           >
//             <ExperimentDetailSection
//               col={col}
//               individualCols={individualCols}
//               row={row}
//               columnConfigs={columnConfig}
//               index={index}
//               refreshGrid={refreshGrid}
//             />
//           </Box>
//         ))}
//       </Box>
//     </Box>
//   </Box>

//   );
// };

// ExperimentDetailDrawerChild.propTypes = {
//   onClose: PropTypes.func,
//   row: PropTypes.object,
//   columnConfig: PropTypes.array,
//   allRows: PropTypes.array,
//   setExpandRow: PropTypes.func,
//   refreshGrid: PropTypes.func,
// };

const ExperimentDetailDrawer = ({
  open,
  onClose,
  row,
  setExpandRow,
  allRows,
  setAllRows,
  totalCount,
  columnConfig,
  refreshGrid,
  diffMode,
  showDiffModeButton = false,
}) => {
  const { experimentId } = useParams();
  const [showDiff, setShowDiff] = useState(false);
  const [nextId, setNextId] = useState(null);
  const [isApiLoading, setIsApiLoading] = useState(false);

  // const { data, isPending, refetch } = useQuery({
  //   queryKey: ["experiment-row-data", experimentId, row?.rowId],
  //   enabled: Boolean(experimentId && row?.rowId),
  //   queryFn: () =>
  //     axios.get(
  //       endpoints.develop.experiment.rowDetail(experimentId, row?.rowId),
  //     ),
  //     select: (d) => d.data?.result,
  //     staleTime: 60000
  //   });

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

  const {
    mutate: setNextItem,
    isPending: isLoadingNextRows,
    isError: isNextRowsError,
    error: nextRowsError,
  } = useMutation({
    meta: { errorHandled: true },
    mutationFn: (rowId) =>
      readLegacyExperimentRow(
        ({ signal, timeout }) =>
          axios.get(
            endpoints.develop.experiment.rowDetail(experimentId, rowId),
            { signal, timeout },
          ),
        rowId,
      ),
    retry: false,
    onSuccess: (newIds) => {
      setNextId(newIds.length > 0 ? newIds[0] : null);
      setAllRows((prev) => {
        const seenIds = new Set(prev.map((item) => String(item?.data?.rowId)));
        const appended = newIds
          .filter((rowId) => !seenIds.has(String(rowId)))
          .map((rowId, index) => ({
            rowIndex: prev.length + index,
            data: { rowId },
          }));
        return [...prev, ...appended];
      });
    },
  });

  // const nextRowId = data?.nextRowId;
  // const prevRowId = data?.prevRowId;

  // Polling for running columns (every 10 seconds) - same as develop dataset
  // useEffect(() => {
  //   const interval = setInterval(() => {
  //     if (isRefreshingColumns.current) {
  //       console.log('Polling: Refetching data for columns:', isRefreshingColumns.current);
  //       refetch();
  //     }
  //   }, 10000); // Poll every 10 seconds

  //   return () => clearInterval(interval);
  // }, [refetch]);

  // Polling for new columns being added (every 5 seconds) - always when drawer is open
  // useEffect(() => {
  //   let pollingInterval;

  //   // Always poll for new columns when drawer is open
  //   if (open && currentRowId) {
  //     pollingInterval = setInterval(async () => {
  //       try {
  //         console.log('Polling: Checking for new columns...');
  //         await refetch();
  //       } catch (error) {
  //         console.warn('Polling error for new columns:', error);
  //       }
  //     }, 5000); // Poll every 5 seconds for new columns
  //   }

  //   return () => {
  //     if (pollingInterval) {
  //       clearInterval(pollingInterval);
  //     }
  //   };
  // }, [open, currentRowId, refetch]);

  // Track column count changes to detect new columns
  // useEffect(() => {
  //   if (data?.columnConfig) {
  //     const currentColumnCount = data.columnConfig.length;
  //     if (lastColumnCount > 0 && currentColumnCount > lastColumnCount) {
  //       console.log(`New columns detected! Count changed from ${lastColumnCount} to ${currentColumnCount}`);
  //       // Optionally trigger a refresh of the parent grid here
  //       if (refreshGrid) {
  //         refreshGrid();
  //       }
  //     }
  //     setLastColumnCount(currentColumnCount);
  //   }
  // }, [data?.columnConfig, lastColumnCount, refreshGrid]);

  // Check if there are any columns that need polling (same logic as develop dataset)
  // useEffect(() => {
  //   if (!data?.columnConfig || !data?.table?.[0]) return;

  //   const rowData = data.table[0];
  //   const columnsNeedingRefresh = [];

  //   // Check each column's status in the actual row data
  //   data.columnConfig.forEach(col => {
  //     const cellData = rowData[col.id];
  //     if (cellData?.status && RefreshStatus.includes(cellData.status)) {
  //       columnsNeedingRefresh.push(col.id);
  //       console.log(`Column ${col.id} needs refresh, status: ${cellData.status}`);
  //     }
  //   });

  //   if (columnsNeedingRefresh.length > 0) {
  //     isRefreshingColumns.current = columnsNeedingRefresh;
  //     console.log('Starting polling for columns:', columnsNeedingRefresh);
  //   } else {
  //     if (isRefreshingColumns.current) {
  //       console.log('Stopping polling - no columns need refresh');
  //     }
  //     isRefreshingColumns.current = null;
  //   }
  // }, [data?.columnConfig, data?.table, RefreshStatus]);

  const { mutate: getDiffData } = useMutation({
    mutationFn: (d) => {
      return axios.post(endpoints.develop.getRowsDiff, d);
    },
    onSuccess: (data, variables) => {
      updateRowFromApiResult(variables.row_ids[0], data?.data?.result);

      const next = data?.data?.result;
      const nextIndex = row?.index + 1;
      setNextId(allRows[nextIndex + 1]?.data?.rowId);
      if (next && row?.rowId) {
        setExpandRow((pre) => {
          return {
            ...pre,
            ...data?.data?.result[variables.row_ids[0]],
            rowId: variables.row_ids[0],
            index: nextIndex,
          };
        });
        // setAllRows((prev) => {
        //   const newData = [...prev, { rowIndex: nextIndex, data: next }];
        //   return newData
        // });
      } else {
        enqueueSnackbar({
          message: "No more datapoint available",
          variant: "error",
        });
      }
      setIsApiLoading(false);
    },
  });

  const { mutate: updateDiffData } = useMutation({
    mutationFn: (d) => {
      return axios.post(endpoints.develop.getRowsDiff, d);
    },
    onSuccess: (data, variables) => {
      updateRowFromApiResult(variables.row_ids[0], data?.data?.result);
      setExpandRow((pre) => {
        return {
          ...pre,
          ...data?.data?.result[variables.row_ids[0]],
          rowId: variables.row_ids[0],
        };
      });
      setIsApiLoading(false);
    },
  });

  const handleToggleDiff = () => {
    setShowDiff((prev) => !prev);
  };

  useEffect(() => {
    const hasDiffValues =
      row &&
      Object.entries(row).some(
        ([_, value]) =>
          typeof value === "object" &&
          value !== null &&
          "cellDiffValue" in value,
      );

    if (hasDiffValues) {
      return;
    }
    if ((showDiff || diffMode) && row) {
      setIsApiLoading(true);
      //@ts-ignore
      updateDiffData({
        experiment_id: `{{${experimentId}}}`,
        column_ids: columnConfig.map((i) => i.id),
        compare_column_ids: columnConfig
          ?.filter((i) => i?.originType == "experiment")
          .map((i) => i?.id),
        row_ids: [row?.rowId],
      });
    }
  }, [showDiff, row?.rowId, diffMode]);

  const handleFetchNextRow = () => {
    const nextIndex = row?.index + 1;
    const nextData = allRows.find((i) => i.rowIndex == nextIndex)?.data;
    const continuationRowId =
      nextId || allRows[allRows?.length - 1]?.data?.rowId;
    if (!nextId && continuationRowId) setNextId(continuationRowId);
    if (nextIndex >= allRows.length - 1 && continuationRowId) {
      setNextItem(continuationRowId);
    }
    if (nextData && Object.entries(nextData).length == 1) {
      // getCellData(payload);
      setIsApiLoading(true);
      getDiffData({
        experiment_id: `{{${experimentId}}}`,
        column_ids: columnConfig.map((i) => i.id),
        compare_column_ids: columnConfig
          ?.filter((i) => i?.originType == "experiment")
          .map((i) => i?.id),
        row_ids: [continuationRowId],
      });
      return;
    }
    if (nextData) {
      setExpandRow((pre) => {
        return {
          ...pre,
          ...nextData,
          index: nextIndex,
        };
      });
    }
  };

  const handleFetchPrevRow = () => {
    const prevIndex = row?.index - 1;
    const prevData = allRows.find((i) => i.rowIndex == prevIndex)?.data;

    if (prevData) {
      setExpandRow((pre) => {
        return {
          ...pre,
          ...prevData,
          index: prevIndex,
        };
      });
    }
  };

  // useEffect(() => {
  //   if (row?.rowId) {
  //     setCurrentRowId(row?.rowId)
  //   }
  // }, [row?.rowId]);

  // useEffect(() => {
  //   if (!experimentId) return;

  //   const rowIdsToPrefetch = [nextRowId, prevRowId].filter(Boolean);

  //   rowIdsToPrefetch.forEach((rowId) => {
  //     queryClient.prefetchQuery({
  //       queryKey: ["experiment-row-data", experimentId, rowId],
  //       queryFn: () =>
  //         axios.get(endpoints.develop.experiment.rowDetail(experimentId, rowId)),
  //       staleTime: 60000,
  //     });
  //   });
  // }, [nextRowId, prevRowId, experimentId]);

  const nextRowId = row?.index >= totalCount - 1;
  const prevRowId = row?.index <= 0;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
          overflowX: "hidden",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {isNextRowsError && (
        <Box sx={{ px: 2, pt: 2 }}>
          <Alert
            severity="error"
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => {
                  const retryRowId =
                    nextId || allRows[allRows?.length - 1]?.data?.rowId;
                  if (retryRowId) setNextItem(retryRowId);
                }}
                disabled={isLoadingNextRows}
              >
                Retry
              </Button>
            }
          >
            {getSafeActionErrorMessage(
              nextRowsError,
              "More experiment rows could not be loaded.",
            )}{" "}
            The current row is still shown.
          </Alert>
        </Box>
      )}
      <ExperimentDetailDrawerContent
        onClose={onClose}
        row={row}
        diffMode={diffMode}
        columnConfig={columnConfig}
        showDiff={showDiff}
        setShowDiff={setShowDiff}
        handleToggleDiff={handleToggleDiff}
        nextRowId={nextRowId}
        prevRowId={prevRowId}
        handleFetchNextRow={handleFetchNextRow}
        handleFetchPrevRow={handleFetchPrevRow}
        isPending={isApiLoading || isLoadingNextRows}
        // handleRefetchRowData={refetch}
        refreshGrid={refreshGrid}
        showDiffModeButton={showDiffModeButton}
      />
    </Drawer>
  );
};

ExperimentDetailDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  row: PropTypes.object,
  columnConfig: PropTypes.array,
  allRows: PropTypes.array,
  setExpandRow: PropTypes.func,
  refreshGrid: PropTypes.func,
  totalCount: PropTypes.number,
  setAllRows: PropTypes.func,
  diffMode: PropTypes.bool,
  showDiffModeButton: PropTypes.bool,
};

export default ExperimentDetailDrawer;
