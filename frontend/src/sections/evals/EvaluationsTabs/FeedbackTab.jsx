import { Box, Typography, IconButton, Skeleton, Chip } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useEffect, useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { useParams } from "react-router";
import {
  interpolateColorBasedOnScore,
  preventHeaderSelection,
} from "src/utils/utils";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";
import _ from "lodash";
import { parseCellValue } from "src/utils/agUtils";
import { AGGridCellDataType } from "src/utils/constant";
import CustomCheckboxEditor from "src/sections/develop-detail/DataTab/CustomCellEditor/CustomCheckboxEditor";
import CustomDevelopGroupCellHeader from "src/sections/common/DevelopCellRenderer/CustomDevelopGroupCellHeader";
import FormattedValueReason from "./FormattedReason";
import logger from "src/utils/logger";
import { APP_CONSTANTS } from "src/utils/constants";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";

const EvaluateArrayCellRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        padding: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "100%",
      }}
    >
      <Box
        sx={{
          lineHeight: "1.5",
          flex: 1,
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          overflow: "hidden",
        }}
      >
        {value?.map((item) => (
          <Chip
            key={item}
            label={item}
            size="small"
            variant="outlined"
            color="primary"
          />
        ))}
      </Box>
    </Box>
  );
};

function convertStringToArray(cellValue) {
  // LLM evals may pass Python-repr strings or {choice: [...]} objects — let the
  // shared helper unwrap to an array when possible.
  const normalized = normalizeEvalCellValue(cellValue);
  if (Array.isArray(normalized)) return normalized;
  if (typeof cellValue !== "string") return [];
  try {
    return JSON.parse(cellValue.replace(/'/g, '"'));
  } catch (error) {
    logger.error("Invalid string format for array:", error);
    return [];
  }
}

const EvaluateCell = ({ value, dataType, cellData }) => {
  const getScorePercentage = (s, decimalPlaces = 0) => {
    if (s <= 0) s = 0;
    const score = parseFloat(s) * 100;
    return Number(score.toFixed(decimalPlaces));
  };

  if (cellData?.output_type == "choices" || dataType == "choices") {
    return <EvaluateArrayCellRenderer value={convertStringToArray(value)} />;
  }

  if (dataType === "boolean") {
    const bgColor = value
      ? value === "Failed"
        ? interpolateColorBasedOnScore(0, 1)
        : interpolateColorBasedOnScore(1, 1)
      : "";
    return (
      <Box
        sx={{
          padding: 1,
          backgroundColor: bgColor,
          color: "text.secondary",
          // flex: 1,
          height: "100%",
        }}
      >
        {_.capitalize(value)}
      </Box>
    );
  }
  if (dataType === "float" || cellData?.output_type == "score") {
    // Eval outputs may arrive as {score, choice} (object or Python-repr string).
    const normalized = normalizeEvalCellValue(cellData?.cell_value);
    const rawScore =
      normalized && typeof normalized === "object" && !Array.isArray(normalized)
        ? typeof normalized.score === "number"
          ? normalized.score
          : NaN
        : parseFloat(normalized);
    const bgColor = cellData
      ? interpolateColorBasedOnScore(isNaN(rawScore) ? 0 : rawScore, 1)
      : "";
    return (
      <Box
        sx={{
          padding: 1,
          backgroundColor: bgColor,
          color: "text.primary",
          height: "100%",
        }}
      >
        {!isNaN(rawScore) ? `${getScorePercentage(rawScore)}%` : ""}
      </Box>
    );
  }

  if (dataType === "array") {
    return <EvaluateArrayCellRenderer value={value} />;
  }

  return (
    <Box
      sx={{
        padding: 1,
        whiteSpace: "pre-wrap",
        lineHeight: "1.5",
        overflow: "hidden",
        textOverflow: "ellipsis",
        display: "-webkit-box",
        WebkitLineClamp: "6",
        WebkitBoxOrient: "vertical",
      }}
    >
      {value}
    </Box>
  );
};

const CustomCellRender = (props) => {
  const dataType = props?.colDef?.data_type;
  const originType = props?.colDef?.origin_type;
  const value = props?.value;
  const cellData = props?.data?.[props?.column?.colId];
  const status = cellData?.status?.toLowerCase();
  const valueReason = value?.reason?.toString();

  if (status === "processing" || value === undefined) {
    return (
      <Box
        sx={{
          paddingX: 1,
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        <Skeleton sx={{ width: "100%", height: "20px" }} variant="rounded" />
      </Box>
    );
  }

  if (status === "error") {
    return (
      <CustomTooltip
        show={Boolean(valueReason?.length)}
        title={FormattedValueReason(valueReason)}
        enterDelay={500}
        arrow
        expandable
      >
        <Box
          sx={{
            color: "error.main",
            opacity: 1,
            flex: 1,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Typography variant="body2" align="center">
            Error
          </Typography>
        </Box>
      </CustomTooltip>
    );
  }

  if (originType === "evaluation" || originType === "optimisation_evaluation") {
    return (
      <CustomTooltip
        show={Boolean(valueReason?.length)}
        title={FormattedValueReason(valueReason)}
        enterDelay={500}
        enterNextDelay={500}
        arrow
        expandable
      >
        <Box sx={{ height: "100%" }}>
          <EvaluateCell
            cellData={cellData}
            value={value}
            dataType={dataType}
            originType={originType}
          />
        </Box>
      </CustomTooltip>
    );
  }

  switch (dataType) {
    case "boolean":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {cellData?.cell_value?.toString()}
        </Box>
      );
    case "text":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object"
            ? cellData?.cell_value?.output || cellData?.cell_value?.input
            : Array.isArray(cellData?.cell_value)
              ? cellData?.cell_value.map((value, index) => (
                  <span style={{ whiteSpace: "pre-wrap" }} key={index}>
                    {value}
                    <br />
                  </span>
                ))
              : cellData?.cell_value}
        </Box>
      );
    case "array":
      break;
    case "choices":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object" ? (
            Array.isArray(cellData?.cell_value) ? (
              <Box sx={{ height: "100%" }}>
                <EvaluateCell
                  cellData={cellData}
                  value={cellData?.cell_value}
                  dataType={dataType}
                  originType={originType}
                />
              </Box>
            ) : (
              cellData?.cell_value?.output
            )
          ) : (
            cellData?.cell_value
          )}
        </Box>
      );
    case "rule_string":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object"
            ? Array.isArray(cellData?.cell_value)
              ? cellData?.cell_value.map((value, index) => (
                  <span key={index}>
                    {value}
                    <br />
                  </span>
                ))
              : cellData?.cell_value?.output || cellData?.cell_value?.input
            : cellData?.cell_value}
        </Box>
      );
    default:
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object"
            ? cellData?.cell_value?.output
            : cellData?.cell_value}
        </Box>
      );
  }
};

const CustomDevelopDetailColumn = (props) => {
  const { displayName, showColumnMenu, col, hideMenu, eGridHeader, api } =
    props;

  const colDef = props?.column?.colDef;
  const refButton = useRef(null);

  useEffect(() => {
    eGridHeader.style.padding = "0px";
  }, [eGridHeader.style]);

  useEffect(() => {
    if (api && col) {
      const minWidth = Math.max(displayName.length * 8 + 50, 250);
      props.api.setColumnWidths([{ key: col.id, newWidth: minWidth }]);
    }
  }, [api, col, displayName]);

  const onMenuClicked = () => {
    showColumnMenu(refButton?.current);
  };

  const renderIcon = () => {
    if (colDef?.headerName === "image_url") {
      return <Iconify icon="material-symbols:image-outline" />;
    } else if (col.originType === "run_prompt") {
      return <Iconify icon="token:rune" sx={{ color: "info.main" }} />;
    } else if (col.originType === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "#22B3B7" }}
        />
      );
    } else if (
      col.originType === "optimisation" ||
      col.originType === "optimisation_evaluation"
    ) {
      return (
        <Iconify
          icon="icon-park-outline:smart-optimization"
          sx={{ color: "primary.main" }}
        />
      );
    } else if (col.originType === "annotation_label") {
      return <Iconify icon="jam:write" />;
    } else if (col.dataType === "text") {
      return <Iconify icon="material-symbols:notes" />;
    } else if (col.dataType === "array") {
      return <Iconify icon="material-symbols:data-array" />;
    } else if (col.dataType === "integer") {
      return <Iconify icon="material-symbols:tag" />;
    } else if (col.dataType === "float") {
      return <Iconify icon="tabler:decimal" />;
    } else if (col.dataType === "boolean") {
      return <Iconify icon="material-symbols:toggle-on-outline" />;
    } else if (col.dataType === "datetime") {
      return <Iconify icon="tabler:calendar" />;
    } else if (col.dataType === "json") {
      return <Iconify icon="material-symbols:data-object" />;
    } else if (col.dataType === "image") {
      return <Iconify icon="material-symbols:image-outline" />;
    } else {
      return <Iconify icon="material-symbols:notes" />;
    }
  };

  const getBackgroundColor = (originType) => {
    if (
      originType === "evaluation" ||
      originType === "optimisation_evaluation"
    ) {
      return "#EEFDFE"; // Same color as evaluation
    } else if (originType === "run_prompt") {
      return "#EEF4FF";
    } else if (originType === "optimisation") {
      return "primary.lighter";
    } else if (originType === "annotation_label") {
      return "#FFE2FE";
    }
    return "background.default";
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        backgroundColor: getBackgroundColor(colDef?.origin_type),
        paddingX: 2,
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {renderIcon()}
        <Typography fontWeight={700} fontSize="13px" color={"text.secondary"}>
          {displayName}
        </Typography>
      </Box>
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="mdi:dots-vertical" />
        </IconButton>
      )}
    </Box>
  );
};

const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
  "processing",
];

const GRID_THEME_PARAMS = {
  columnBorder: true,
  rowVerticalPaddingScale: 3,
};

const FeedbackTab = () => {
  const agTheme = useAgThemeWith(GRID_THEME_PARAMS);
  const [selected, setSelected] = useState([]);
  const gridRef = useRef(null);
  const { evalId } = useParams();
  const [isRefreshing, setIsRefreshing] = useState(null);
  const [, setRowData] = useState([]);
  const [, setIsData] = useState(false);
  const [columnDefs, setColumnDefs] = useState([
    {
      headerName: "Column 1",
      field: "name",
      flex: 1,
    },
    {
      headerName: "Column 2",
      field: "numberOfDatapoints",
      flex: 1,
    },
    {
      headerName: "Column 3",
      field: "numberOfExperiments",
      flex: 1,
    },
    {
      headerName: "Column 4",
      field: "numberOfOptimisations",
      flex: 1,
    },
    {
      headerName: "Column 4",
      field: "numberOfOptimisations",
      flex: 1,
    },
  ]);
  const [selectedAll, setSelectedAll] = useState(false);
  const [columnData] = useState([]);

  const [searchQuery] = useState("");

  const debouncedSearchQuery = useDebounce(searchQuery, 500);
  preventHeaderSelection();

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;
        onSelectionChanged(null);
        setSelectedAll(false);
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);
        try {
          const { data } = await axios.post(
            endpoints.develop.eval.getEvalFeedbacks,
            {
              eval_template_id: evalId,
              current_page_index: pageNumber,
              page_size: pageSize,
              sort: request?.sortModel?.map(({ colId, sort }) => ({
                column_id: colId,
                type: sort === "asc" ? "ascending" : "descending",
              })),
            },
          );
          setColumnDataNew(data?.result?.column_config);
          const rows = data?.result?.table;
          setRowData(rows);
          if (!rows || rows.length === 0) {
            setTimeout(() => {
              if (gridRef.current?.api) {
                gridRef.current.api.showNoRowsOverlay();
              }
            }, 0);
          } else {
            if (gridRef.current?.api) {
              gridRef.current.api.hideOverlay();
            }
          }

          if (rows.length >= 1) {
            setIsData(true);
          }
          params.success({
            rowData: rows,
            rowCount: data?.result?.metadata?.total_rows,
          });
          if (rows?.length === 0) {
            setTimeout(() => {
              if (gridRef.current?.api) {
                gridRef.current.api.showNoRowsOverlay();
              }
            }, 0);
          }
        } catch (error) {
          setIsRefreshing(null);
          params.fail();
          setTimeout(() => {
            if (gridRef.current?.api) {
              gridRef.current.api.showNoRowsOverlay();
            }
          }, 0);
        }
      },
      getRowId: (data) => data.rowId,
    }),
    [debouncedSearchQuery, evalId],
  );

  useEffect(() => {
    if (gridRef.current?.api) {
      const rowCount = gridRef.current.api.getDisplayedRowCount();
      if (rowCount === 0) {
        gridRef.current.api.showNoRowsOverlay();
      } else {
        gridRef.current.api.hideOverlay();
      }
    }
  }, [columnData]);

  // Column Definitions
  const setColumnDataNew = (data, setCols = true, setRefresh = true) => {
    const columns = data;

    if (columns.length == 0) {
      setIsData(false);
    }

    const grouping = {};

    for (const eachCol of columns) {
      if (
        eachCol?.origin_type === "evaluation" ||
        eachCol?.origin_type === "evaluation_reason"
      ) {
        if (!grouping[eachCol?.id]) {
          grouping[eachCol?.id] = [eachCol];
        } else {
          grouping[eachCol?.id].push(eachCol);
        }
      } else {
        grouping[eachCol?.id] = [eachCol];
      }
    }

    const columnMap = [];
    const bottomRow = {};

    const refresh = [];

    for (const [_, cols] of Object.entries(grouping)) {
      if (cols.length === 1) {
        const eachCol = cols[0];
        columnMap.push({
          field: eachCol.id,
          headerName: eachCol.name,
          valueGetter: (v) =>
            parseCellValue(
              v.data?.[eachCol.id]?.cell_value,
              AGGridCellDataType[eachCol.dataType],
            ),
          valueSetter: (params) => {
            params.data[eachCol.id].cell_value = params.newValue;
            return true;
          },
          editable: false,
          cellDataType: AGGridCellDataType[eachCol.dataType],
          dataType: eachCol.dataType,
          pinned: eachCol?.is_frozen,
          hide: !eachCol?.is_visible,
          sortable: true,
          // minWidth: 250,
          filter: false,
          resizable: true,
          cellStyle: {
            padding: 0,
            height: "100%",
            display: "flex",
            // flex: 1,
            flexDirection: "column",
          },
          // suppressSizeToFit: true,
          originType: eachCol?.origin_type,
          headerComponent: CustomDevelopDetailColumn,
          headerComponentParams: {
            col: eachCol,
          },
          col: {
            ...eachCol,
          },
          cellEditor:
            eachCol?.data_type === "boolean" ? CustomCheckboxEditor : undefined,
          cellRenderer: CustomCellRender,
          headerGroupComponent: CustomDevelopGroupCellHeader,
          headerGroupComponentParams: {
            col: eachCol,
          },
          headerClass: "develop-data-group-header",
        });

        if (RefreshStatus.includes(eachCol?.status)) {
          refresh.push(eachCol.id);
        }
        bottomRow[eachCol.id] = eachCol?.average_score
          ? `Average : ${eachCol?.average_score}%`
          : "";
      } else {
        const eachCol = cols[0];

        columnMap.push({
          field: eachCol.id,
          headerName: eachCol.name,
          valueGetter: (v) =>
            parseCellValue(
              v.data?.[eachCol.id]?.cell_value,
              AGGridCellDataType[eachCol.dataType],
            ),
          valueSetter: (params) => {
            params.data[eachCol.id].cell_value = params.newValue;
            return true;
          },
          editable: false,
          cellDataType: AGGridCellDataType[eachCol.dataType],
          dataType: eachCol.dataType,
          pinned: eachCol?.is_frozen,
          hide: !eachCol?.is_visible,
          sortable: true,
          filter: false,
          resizable: true,
          cellStyle: {
            padding: 0,
            height: "100%",
            display: "flex",
            // flex: 1,
            flexDirection: "column",
          },
          originType: eachCol?.origin_type,
          headerComponent: CustomDevelopDetailColumn,
          headerComponentParams: {
            col: eachCol,
          },
          col: {
            ...eachCol,
          },
          cellEditor:
            eachCol?.data_type === "boolean" ? CustomCheckboxEditor : undefined,
          cellRenderer: CustomCellRender,
          headerGroupComponent: CustomDevelopGroupCellHeader,
          headerGroupComponentParams: {
            col: eachCol,
          },
          headerClass: "develop-data-group-header",
        });
        if (RefreshStatus.includes(eachCol?.status)) {
          refresh.push(eachCol.id);
        }
        bottomRow[eachCol.id] = eachCol?.average_score
          ? `Average : ${eachCol?.average_score}%`
          : "";
      }
    }

    if (setRefresh) {
      if (refresh.length > 0) {
        if (!isRefreshing) setIsRefreshing(refresh);
      } else {
        setIsRefreshing(null);
      }
    }

    if (setCols) {
      setColumnDefs([...columnMap]);
    }

    return refresh;
  };

  const refreshRowsManual = async () => {
    const totalPages = Object.keys(
      gridRef?.current?.api?.getCacheBlockState(),
    ).length;

    for (let p = 0; p < totalPages; p++) {
      try {
        const { data } = await axios.post(
          endpoints.develop.eval.getEvalFeedbacks,
          {
            eval_template_id: evalId,
            current_page_index: p,
          },
        );

        setColumnDataNew(data, false, true, true);

        const rows = data?.result?.table;
        setRowData(rows);
        const transaction = {
          update: rows,
        };
        if (gridRef.current?.api) {
          gridRef.current.api.applyServerSideTransaction(transaction);
        }
      } catch (e) {
        logger.error("Failed to refresh rows", e);
      }
    }
  };

  useEffect(() => {
    const interval = setInterval(() => {
      if (isRefreshing) {
        refreshRowsManual();
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [isRefreshing]);

  const onSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelected([]);
      }, 300);
      gridRef.current.api.deselectAll();
      return;
    }
    const rowId = event.data.id;

    setSelected((prevSelectedItems) => {
      const updatedSelectedRowsData = [...prevSelectedItems];

      const rowIndex = updatedSelectedRowsData.findIndex(
        (row) => row.id === rowId,
      );

      if (rowIndex === -1) {
        updatedSelectedRowsData.push(event.data);
      } else {
        updatedSelectedRowsData.splice(rowIndex, 1);
      }

      return updatedSelectedRowsData;
    });
  };

  const closeModal = () => {
    onSelectionChanged(null);
    gridRef.current.api.deselectAll();
    setSelectedAll(false);
  };
  const noRowsTemplate =
    '<div style="padding: 10px;font-size:13px;font-family:"inherit""><span>No feedback data available</span></div>';

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        paddingX: "12px",
        paddingTop: "10px",
        paddingBottom: 2,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box>
          {selected?.length ? (
            <Box
              sx={{
                padding: "6px 16px",
                gap: "16px",
                borderRadius: "8px",
                border: "1px solid rgba(225, 223, 236, 1)",
                display: "flex",
              }}
            >
              <Typography
                sx={{
                  fontSize: "14px",
                  fontWeight: 500,
                  lineHeight: "22px",
                  color: "rgba(120, 87, 252, 1)",
                  paddingRight: "16px",
                  borderRight: "2px solid rgba(225, 223, 236, 1)",
                }}
              >
                {selected?.length || 0} Selected
              </Typography>
              {/* <Typography
                                sx={{
                                    fontSize: "14px",
                                    fontWeight: 600,
                                    color: "text.secondary",
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "5px",
                                    cursor: "pointer",
                                }}
                                onClick={() => setOpenDelete(true)}
                            >
                                <Iconify icon="solar:trash-bin-trash-bold" />
                                Delete
                            </Typography> */}

              <Typography
                sx={{
                  fontSize: "14px",
                  fontWeight: 600,
                  color: "text.secondary",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer",
                }}
                onClick={closeModal}
              >
                Cancel
              </Typography>
            </Box>
          ) : (
            <></>
          )}
        </Box>
      </Box>
      <Box className="ag-theme-quartz" style={{ height: "100%" }}>
        <AgGridReact
          ref={gridRef}
          getRowHeight={(params) => {
            return params.node.rowPinned === "bottom" ? 40 : 120;
          }}
          onColumnHeaderClicked={(event) => {
            if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
              return;

            if (selectedAll) {
              event.api.deselectAll();
              setSelectedAll(false);
            } else {
              event.api.selectAll();
              setSelectedAll(true);
            }
          }}
          rowSelection="none"
          theme={agTheme}
          columnDefs={columnDefs}
          // defaultColDef={defaultColDef}
          pagination={false}
          cacheBlockSize={10}
          maxBlocksInCache={10}
          suppressRowClickSelection={true}
          rowModelType="serverSide"
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={6}
          overlayNoRowsTemplate={noRowsTemplate}
          serverSideDatasource={dataSource}
          isApplyServerSideTransaction={() => true}
          onRowSelected={(event) => onSelectionChanged(event)}
          getRowId={({ data }) => data.rowId}
          onCellClicked={(event) => {
            if (
              event.column.getColId() !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              // condition here
            } else if (
              event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              const selected = event.node.isSelected();
              event.node.setSelected(!selected);
            }
          }}
        />
      </Box>
    </Box>
  );
};

EvaluateCell.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  cellData: PropTypes.object,
  originType: PropTypes.string,
};

EvaluateArrayCellRenderer.propTypes = {
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  value: PropTypes.any,
};

CustomCellRender.propTypes = {
  column: PropTypes.object,
  value: PropTypes.any,
  data: PropTypes.object,
  node: PropTypes.any,
  colDef: PropTypes.any,
};

CustomDevelopDetailColumn.propTypes = {
  displayName: PropTypes.string.isRequired,
  eSort: PropTypes.object,
  eMenu: PropTypes.object,
  eFilterButton: PropTypes.object,
  eFilter: PropTypes.object,
  eSortOrder: PropTypes.object,
  eSortAsc: PropTypes.object,
  eSortDesc: PropTypes.object,
  eSortNone: PropTypes.object,
  eText: PropTypes.object,
  menuButtonRef: PropTypes.object,
  filterButtonRef: PropTypes.object,
  sortOrderRef: PropTypes.object,
  sortAscRef: PropTypes.object,
  sortDescRef: PropTypes.object,
  sortNoneRef: PropTypes.object,
  filterRef: PropTypes.object,
  showColumnMenu: PropTypes.func,
  col: PropTypes.object,
  column: PropTypes.object,
  hideMenu: PropTypes.bool,
  eGridHeader: PropTypes.any,
  api: PropTypes.any,
};

export default FeedbackTab;
