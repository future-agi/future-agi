import { Box, Button, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "react-router";
import SingleImageViewerProvider from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import CustomCheckboxEditor from "src/sections/develop-detail/DataTab/CustomCellEditor/CustomCheckboxEditor";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { parseCellValue } from "src/utils/agUtils";
import axios, { endpoints } from "src/utils/axios";
import { AGGridCellDataType } from "src/utils/constant";
import { defaultRowHeightMapping } from "src/utils/constants";
import DevelopFilterBox from "../../DevelopFilters/DevelopFilterBox";
import { getRandomId } from "src/utils/utils";
import {
  DefaultFilter,
  transformFilter,
  validateFilter,
} from "../../DevelopFilters/common";
import ColumnDropdown from "src/components/ColumnDropdown/ColumnDropdown";
import { useMutation } from "@tanstack/react-query";
import debounce from "lodash/debounce";
import { useDebounce } from "src/hooks/use-debounce";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  CustomCellRender,
  CustomDevelopDetailColumn,
} from "./CellRenderingData";
import CustomDevelopGroupCellHeader from "src/sections/common/DevelopCellRenderer/CustomDevelopGroupCellHeader";
import LogsDrawer from "./LogsDrawer";
import NoResultsUI from "./NoResultsUI";
import TableFilterOptions from "src/components/TableFilterOptions/TableFilterOptions";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { APP_CONSTANTS } from "src/utils/constants";

const FeedbackOverlay = () => (
  <NoResultsUI
    title="No feedback entries yet"
    description="Feedback from test logs will appear here"
  />
);

const EvaluationOverlay = () => (
  <NoResultsUI
    title="No evaluations has been logged"
    description="Test evaluations on the playground section to view the logs"
  />
);

const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
  "processing",
];

const defaultColDef = {
  lockVisible: true,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
  cellStyle: {
    padding: 0,
    height: "100%",
    display: "flex",
    flex: 1,
    flexDirection: "column",
  },
};

const sampleColumnDef = [
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
];

const GRID_THEME_PARAMS = {
  columnBorder: true,
  rowVerticalPaddingScale: 3,
};

const LogsTabGrid = ({
  isFeedback = false,
  isEvalPlayGround = false,
  tableRef,
  templateId,
  dateFilter,
}) => {
  const agTheme = useAgThemeWith(GRID_THEME_PARAMS);
  const gridRef = useRef(null);
  const resizerRef = useRef(null);
  const columnConfigureRef = useRef(null);
  const { role } = useAuthContext();
  const { evalId } = useParams();
  const evalsId = evalId || templateId;
  const [selectedAll, setSelectedAll] = useState(false);
  const [openDelete, setOpenDelete] = useState(false);
  const [isData, setIsData] = useState(false);
  const [cellHeight, setCellHeight] = useState("Short");
  const [columnDefs, setColumnDefs] = useState([...sampleColumnDef]);
  const [selectedItems, setSelectedItems] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [isFilterApplied, setIsFilterApplied] = useState(false);
  const [developFilterOpen, setDevelopFilterOpen] = useState(false);
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const [columns, setColumns] = useState([]);
  const [selectedRow, setSelectedRow] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  const [filters, setFilters] = useState([
    { ...DefaultFilter, id: getRandomId() },
  ]);

  useEffect(() => {
    const hasActiveFilter = filters?.some((f) =>
      f.filterConfig?.filterValue && Array.isArray(f.filterConfig.filterValue)
        ? f.filterConfig.filterValue.length > 0
        : f.filterConfig.filterValue !== "",
    );
    setIsFilterApplied(hasActiveFilter);
  }, [filters]);

  useMemo(() => {
    if (!columns?.length || !columnDefs?.length) return;

    const updatedDefs = reorderColumnDefsBasedOnColumns(columnDefs, columns);
    setColumnDefs(updatedDefs);
  }, [columns]);

  const allColumns = useMemo(() => {
    return columnDefs.filter((col) => col.field !== "checkbox" && !col.hide);
  }, [columnDefs]);

  const evalOutputTypes = useMemo(() => {
    return columns.reduce((acc, col) => {
      if (col.origin_type === "evaluation" && col.output_type) {
        acc[col.id] = col.output_type;
      }
      return acc;
    }, {});
  }, [columns]);

  const refreshGrid = (option = {}) => {
    if (gridRef.current?.api) {
      gridRef.current.api.refreshServerSide({ force: true, ...option });
    }
  };

  const setColumnDataNew = (
    data,
    setCols = true,
    // setRefresh = true
  ) => {
    const columns = data;
    setColumns(columns);
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

    for (const [, cols] of Object.entries(grouping)) {
      if (cols.length === 1) {
        const eachCol = cols[0];
        columnMap.push({
          field: eachCol.id,
          headerName: eachCol.name,
          valueGetter: (v) =>
            parseCellValue(
              v.data?.[eachCol.id]?.cell_value,
              AGGridCellDataType[eachCol.data_type],
            ),
          valueSetter: (params) => {
            params.data[eachCol.id].cell_value = params.newValue;
            return true;
          },
          editable: false,
          cellDataType: AGGridCellDataType[eachCol.data_type],
          dataType: eachCol.data_type,
          pinned: eachCol?.is_frozen,
          hide: !eachCol?.is_visible,
          sortable: eachCol.name === "Created At",
          filter: false,
          resizable: true,
          cellStyle: {
            padding: 0,
            height: "100%",
            display: "flex",
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
            eachCol?.dataType === "boolean" ? CustomCheckboxEditor : undefined,
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
          sortable: eachCol.name === "Created At",
          valueGetter: (v) =>
            parseCellValue(
              v.data?.[eachCol.id]?.cell_value,
              AGGridCellDataType[eachCol.data_type],
            ),
          valueSetter: (params) => {
            params.data[eachCol.id].cell_value = params.newValue;
            return true;
          },
          editable: false,
          cellDataType: AGGridCellDataType[eachCol.data_type],
          dataType: eachCol.data_type,
          pinned: eachCol?.is_frozen,
          hide: !eachCol?.is_visible,
          resizable: true,
          filter: false,
          cellStyle: {
            padding: 0,
            height: "100%",
            display: "flex",
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
            eachCol?.dataType === "boolean" ? CustomCheckboxEditor : undefined,
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

    if (setCols) {
      setColumnDefs([...columnMap]);
    }

    return refresh;
  };

  const validatedFilters = useMemo(() => {
    return filters.filter(validateFilter).map(transformFilter);
  }, [filters]);

  const serializedFilters = useMemo(
    () => JSON.stringify(validatedFilters),
    [validatedFilters],
  );
  const debouncedSerializedFilters = useDebounce(serializedFilters, 500);
  const debouncedFilters = useMemo(
    () => JSON.parse(debouncedSerializedFilters),
    [debouncedSerializedFilters],
  );

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        setIsLoading(true);
        const { request } = params;
        onSelectionChanged(null);
        setSelectedAll(false);

        // Calculate page size dynamically from AG Grid request
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor((request?.startRow ?? 0) / pageSize);
        const source = isFeedback
          ? "feedback"
          : isEvalPlayGround
            ? "eval_playground"
            : "logs";

        const search = searchQuery
          ? {
              key: searchQuery,
              type: ["text", "image", "audio"],
            }
          : undefined;

        const filters = [];
        const createdAtColumn = columnDefs
          ?.filter((col) => col?.field !== "checkbox")
          ?.find((item) => item?.headerName === "Created At");
        const createdAtFieldId = createdAtColumn?.field || "created_at";
        // Skip default dateFilter if user already has a Created At filter applied
        const hasCreatedAtFilter = debouncedFilters.some(
          (f) => f.column_id === createdAtFieldId,
        );
        if (dateFilter && !hasCreatedAtFilter) {
          filters.push({
            column_id: createdAtFieldId,
            filter_config: dateFilter.filter_config,
          });
        }
        if (debouncedFilters.length) {
          filters.push(...debouncedFilters);
        }
        try {
          const { data } = await axios.get(
            endpoints.develop.eval.getEvalsLogs,
            {
              params: {
                eval_template_id: evalsId,
                current_page_index: pageNumber,
                page_size: pageSize,
                filters: JSON.stringify(filters),
                search: search,
                sort: JSON.stringify(
                  request?.sortModel?.map(({ colId, sort }) => ({
                    column_id: colId,
                    type: sort === "asc" ? "ascending" : "descending",
                  })),
                ),
                source: source,
              },
            },
          );

          setColumnDataNew(data?.result?.column_config);
          const rows = data?.result?.table;

          if (rows.length >= 1 || pageNumber > 0 || searchQuery) {
            setIsData(true);
          }
          params.success({
            rowData: rows,
            rowCount: data?.result?.metadata?.total_rows || 0,
          });
          if (rows?.length === 0 && !data?.result?.metadata?.total_rows) {
            params.api?.showNoRowsOverlay();
          } else {
            params.api.hideOverlay();
          }
        } catch (error) {
          params.fail();
          params.api?.showNoRowsOverlay();
        } finally {
          setIsLoading(false);
        }
      },
      getRowId: (data) => data.rowId,
    }),
    [evalsId, debouncedFilters, searchQuery, dateFilter],
  );

  const closeModal = () => {
    setOpenDelete(false);
  };

  const onSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelectedItems([]);
        setSelectedAll(false);
      }, 300);
      gridRef.current?.api?.deselectAll();
      return;
    }
    const rowId = event.data.rowId;

    setSelectedItems((prevSelectedItems) => {
      const rowMap = new Map(
        prevSelectedItems.map((item) => [item.rowId, item]),
      );

      if (rowMap.has(rowId)) {
        rowMap.delete(rowId);
      } else {
        rowMap.set(rowId, event.data);
      }

      const updatedSelectedRowsData = Array.from(rowMap.values());

      return updatedSelectedRowsData;
    });
  };

  const { mutate: updateColumnVisibility } = useMutation({
    mutationFn: (data) => axios.patch(endpoints.develop.eval.getEvalLogs, data),
    onSuccess: () => {
      refreshGrid();
    },
  });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const debouncedUpdateColumnVisibility = useCallback(
    debounce((data) => {
      updateColumnVisibility(data);
    }, 1000),
    [],
  );

  const onColumnVisibilityChange = (columnId) => {
    const newColumnData = columns.map((col) => {
      if (col.id !== columnId) return col;
      // Toggle both snake_case (canonical) and camelCase (alias) so
      // downstream reads see the same value regardless of which form
      // they use. Without this, `col.is_visible` would remain stale.
      const next = !(col.is_visible ?? col.isVisible);
      return { ...col, is_visible: next, isVisible: next };
    });

    const updatedDefs = columnDefs.map((colDef) =>
      colDef.field === columnId ? { ...colDef, hide: !colDef.hide } : colDef,
    );

    // Directly update state
    setColumns(newColumnData);
    setColumnDefs(updatedDefs);
    const source = isFeedback
      ? "feedback"
      : isEvalPlayGround
        ? "eval_playground"
        : "logs";

    const finalData = {
      eval_id: evalsId,
      column_config: newColumnData,
      source,
    };

    debouncedUpdateColumnVisibility(finalData);
  };

  const onColumnChanged = useCallback(
    (params) => {
      if (!params.finished && params.type === "columnMoved") {
        return;
      }

      const newColumnOrder = params.api
        .getColumnState()
        .filter(
          ({ colId }) => colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN,
        );

      const orderedColumn = newColumnOrder.map((col) => {
        return columns.find((column) => column.id === col.colId);
      });

      const filteredData = orderedColumn.filter((item) => Boolean(item));
      if (filteredData.length > 0) {
        const source = isFeedback
          ? "feedback"
          : isEvalPlayGround
            ? "eval_playground"
            : "logs";
        const finalData = {
          eval_id: evalsId,
          column_config: orderedColumn,
          source,
        };
        debouncedUpdateColumnVisibility(finalData);
      }
    },
    [columns],
  );

  function reorderColumnDefsBasedOnColumns(columnDefs, columns) {
    const idToIndex = new Map(columns.map((col, index) => [col.id, index]));
    const defMap = new Map(columnDefs.map((def) => [def.field, def]));

    const updatedColumnDefs = [];

    for (let i = 0; i < columns.length; i++) {
      const colId = columns[i].id;
      const def = defMap.get(colId);

      if (def) {
        const updatedDef = {
          ...def,
          col: def.col ? { ...def.col, orderIndex: i } : def.col,
          headerComponentParams: def.headerComponentParams
            ? {
                ...def.headerComponentParams,
                col: {
                  ...def.headerComponentParams.col,
                  orderIndex: i,
                },
              }
            : def.headerComponentParams,
        };
        updatedColumnDefs.push(updatedDef);
      }
    }

    // Add unmatched columnDefs at the end (if any)
    for (const def of columnDefs) {
      if (!idToIndex.has(def.field)) {
        updatedColumnDefs.push(def);
      }
    }

    return updatedColumnDefs;
  }

  const handleDelete = () => {
    const payload = { log_ids: selectedItems.map((item) => item.logId) };
    // @ts-ignore
    deleteMutate(payload);
  };

  const { mutate: deleteMutate } = useMutation({
    mutationFn: (data) =>
      axios.delete(endpoints.develop.eval.getEvalLogs, { data }),
    onSuccess: () => {
      closeModal();
      onSelectionChanged(null);
      refreshGrid();
    },
  });

  const onGridReady = useCallback(
    (params) => {
      params.api.setGridOption("serverSideDatasource", dataSource);

      dataSource.getRows({
        success: ({ rowData }) => {
          if (rowData.length > 0) {
            setIsData(true);
          } else {
            setIsData(false);
          }
          if (rowData?.length === 0) {
            params.api.showNoRowsOverlay();
          } else {
            params.api.hideOverlay();
          }
        },
        fail: () => params.api.showNoRowsOverlay(),
      });
    },
    [dataSource],
  );

  const NoRowOverLayComponent = useMemo(() => {
    if (isLoading) return null;
    if (!isData) return isFeedback ? FeedbackOverlay : EvaluationOverlay;
    return null;
  }, [isData, isLoading, isFeedback]);

  useEffect(() => {
    if (gridRef.current?.api && !isData) {
      gridRef.current.api.showNoRowsOverlay();
      // hide overlay when data is being fetched
      if (isLoading) {
        gridRef.current.api.hideOverlay();
      }
    }
  }, [NoRowOverLayComponent, isData, isLoading]);

  return (
    <Box height="100%">
      <Box
        display={"flex"}
        justifyContent={"space-between"}
        alignItems={"center"}
        padding={"16px 0px"}
      >
        <ShowComponent condition={!isFeedback}>
          <Typography
            typography="m3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            {isEvalPlayGround ? "Playground Logs" : "Evaluation Logs"}
          </Typography>
        </ShowComponent>
        <Box display={selectedItems.length === 0 ? "flex" : "none"}>
          <TableFilterOptions
            gridApiRef={gridRef}
            resizerRef={resizerRef}
            columnConfigureRef={columnConfigureRef}
            setDevelopFilterOpen={setDevelopFilterOpen}
            setOpenColumnConfigure={setOpenColumnConfigure}
            setCellHeight={setCellHeight}
            setSearchQuery={setSearchQuery}
            isFilterApplied={isFilterApplied}
            isData={isData}
          />
        </Box>
        <Box
          sx={{
            display: selectedItems.length > 0 ? "flex" : "none",
            justifyContent: "flex-end",
            alignItems: "center",
            ...(isFeedback && { width: "100%" }),
          }}
        >
          <Box
            sx={{
              padding: "4px 16px",
              gap: "16px",
              borderRadius: "4px",
              border: "1px solid",
              borderColor: "divider",
              display: "flex",
            }}
          >
            <Typography
              typography="s1"
              fontWeight={"fontWeightMedium"}
              color="primary.main"
              sx={{
                paddingRight: "16px",
                borderRight: "1px solid",
                borderColor: "divider",
              }}
            >
              {selectedItems?.length || 0} Selected
            </Typography>
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
              sx={{
                display: "flex",
                alignItems: "center",
                gap: "5px",
                cursor: "pointer",
              }}
              onClick={() => {
                if (
                  RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][
                    role
                  ]
                ) {
                  setOpenDelete(true);
                }
              }}
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{ width: 19, height: 19, color: "text.primary" }}
              />
              Delete
            </Typography>

            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
              sx={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                cursor: "pointer",
                paddingLeft: "16px",
                borderLeft: "1px solid",
                borderColor: "divider",
              }}
              onClick={() => onSelectionChanged(null)}
            >
              Cancel
            </Typography>
          </Box>
        </Box>
      </Box>
      <Box>
        <DevelopFilterBox
          setDevelopFilterOpen={setDevelopFilterOpen}
          developFilterOpen={developFilterOpen}
          filters={filters}
          setFilters={setFilters}
          allColumns={allColumns}
        />
      </Box>
      <Box className="ag-theme-quartz" style={{ height: "calc(100% - 65px)" }}>
        <SingleImageViewerProvider>
          <AgGridReact
            onGridReady={onGridReady}
            theme={agTheme}
            ref={(params) => {
              gridRef.current = params;
              if (tableRef) tableRef.current = params;
            }}
            rowHeight={defaultRowHeightMapping[cellHeight]?.height}
            getRowHeight={(params) => {
              return params.node.rowPinned === "bottom"
                ? 40
                : defaultRowHeightMapping[cellHeight]?.height;
            }}
            rowSelection={{ mode: "multiRow" }}
            selectionColumnDef={{ pinned: "left" }}
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
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            suppressContextMenu={true}
            pagination={false}
            cacheBlockSize={10}
            maxBlocksInCache={10}
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={10}
            rowModelType="serverSide"
            // statusBar={statusBar}
            suppressRowClickSelection={true}
            isApplyServerSideTransaction={() => true}
            // overlayNoRowsTemplate={noRowsTemplate}
            onColumnMoved={onColumnChanged}
            onRowSelected={(event) => onSelectionChanged(event)}
            getRowId={({ data }) => data.rowId}
            onCellClicked={(params) => {
              if (
                params?.column?.getColId() ===
                APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
              ) {
                const selected = params.node.isSelected();
                params.node.setSelected(!selected);
                return;
              } else {
                if (window.__audioClick) {
                  return;
                }
                if (window.__imageClick) return (window.__imageClick = false);
                const { data } = params;
                if (data?.rowId) {
                  setSelectedRow(data);
                }
              }
            }}
            noRowsOverlayComponent={NoRowOverLayComponent}
          />
        </SingleImageViewerProvider>
      </Box>
      <LogsDrawer
        open={Boolean(selectedRow) && !isFeedback}
        onClose={() => setSelectedRow(null)}
        selectedRow={selectedRow}
        evalsId={evalsId}
        refreshGrid={refreshGrid}
        evalOutputTypes={evalOutputTypes}
      />
      <ColumnDropdown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={columns}
        onColumnVisibilityChange={onColumnVisibilityChange}
        setColumns={setColumns}
        defaultGrouping="Data columns"
      />
      <ConfirmDialog
        content="Are you sure you want to delete?"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={handleDelete}
          >
            Confirm
          </Button>
        }
        open={openDelete}
        onClose={() => setOpenDelete(false)}
        title="Confirm Delete"
        message="Are you sure you want to delete?"
      />
    </Box>
  );
};

export default LogsTabGrid;

LogsTabGrid.propTypes = {
  isFeedback: PropTypes.bool,
  isEvalPlayGround: PropTypes.bool,
  tableRef: PropTypes.object,
  templateId: PropTypes.string,
  dateFilter: PropTypes.object,
};
