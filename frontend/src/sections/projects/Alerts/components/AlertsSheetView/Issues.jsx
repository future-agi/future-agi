import {
  Badge,
  Box,
  Button,
  Divider,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { ShowComponent } from "src/components/show";
import SheetFilters from "./SheetFilters";
import ColumnDropdown from "src/components/ColumnDropdown/ColumnDropdown";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { issueColumns } from "../../common";
import { camelCase } from "lodash";
import { useAlertStore } from "../../store/useAlertStore";
import { useAlertSheetView } from "../../store/useAlertSheetView";
import { useAlertSheetFilterShallow } from "../../store/useAlertSheetFilterStore";
import logger from "src/utils/logger";
import { APP_CONSTANTS } from "src/utils/constants";

const TableAction = () => {
  const { hasValidFilters, toggleFilter } = useAlertSheetFilterShallow();
  const { setColumnDefs, columnDefs, setColumns, columns } =
    useAlertSheetView();
  const columnConfigureRef = useRef();
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);

  const onColumnVisibilityChange = (columnId) => {
    const newColumnData = columns.map((col) =>
      col.id === columnId ? { ...col, isVisible: !col.isVisible } : col,
    );

    const updatedDefs = columnDefs.map((colDef) =>
      colDef.field === columnId ? { ...colDef, hide: !colDef.hide } : colDef,
    );

    setColumns(newColumnData);
    setColumnDefs(updatedDefs);
  };

  function reorderColumnDefsBasedOnColumns(columnDefs, columns) {
    const idToIndex = new Map(columns?.map((col, index) => [col.id, index]));
    const defMap = new Map(columnDefs?.map((def) => [def.field, def]));

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

  useEffect(() => {
    if (!columns?.length || !columnDefs?.length) return;

    const updatedDefs = reorderColumnDefsBasedOnColumns(columnDefs, columns);
    setColumnDefs(updatedDefs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns]);

  return (
    <>
      <Stack direction={"row"} gap={2.5}>
        <IconButton
          onClick={toggleFilter}
          size="small"
          sx={{
            color: "text.primary",
          }}
        >
          {hasValidFilters ? (
            <Badge
              variant="dot"
              color="error"
              overlap="circular"
              anchorOrigin={{ vertical: "top", horizontal: "right" }}
              sx={{
                "& .MuiBadge-badge": {
                  top: 1,
                  right: 1,
                },
              }}
            >
              <SvgColor
                src="/assets/icons/components/ic_filter.svg"
                sx={{ height: 16, width: 16 }}
              />
            </Badge>
          ) : (
            <SvgColor
              src="/assets/icons/components/ic_filter.svg"
              sx={{ height: 16, width: 16 }}
            />
          )}
        </IconButton>
        <IconButton
          ref={columnConfigureRef}
          onClick={() => setOpenColumnConfigure(true)}
          size="small"
          sx={{
            color: "text.primary",
          }}
        >
          <SvgColor
            sx={{
              height: 16,
              width: 16,
            }}
            src="/assets/icons/action_buttons/ic_column.svg"
          />
        </IconButton>
      </Stack>
      <ColumnDropdown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={columns}
        onColumnVisibilityChange={onColumnVisibilityChange}
        setColumns={setColumns}
        defaultGrouping="Data columns"
      />
    </>
  );
};

const RowActions = () => {
  const theme = useTheme();
  const {
    selectedRows,
    handleCancelSelection,
    handleResolveAlerts,
    isResolving,
    selectedAll,
    totalRows,
    excludingIds,
  } = useAlertSheetView();

  const message = selectedAll
    ? `${totalRows - excludingIds?.length} Selected`
    : `${selectedRows.length} Selected`;

  return (
    <Stack
      direction={"row"}
      gap={2}
      alignItems={"center"}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        padding: theme.spacing(0, 2),
        height: "38px",
      }}
    >
      <Typography
        variant="s1"
        fontWeight={"fontWeightMedium"}
        color={"primary.main"}
      >
        {message}
      </Typography>
      <Divider
        orientation="vertical"
        sx={{
          borderColor: "divider",
          height: "80%",
        }}
      />
      <Button
        variant="text"
        size="small"
        onClick={handleResolveAlerts}
        disabled={isResolving}
        sx={{
          mr: 1,
          typography: "s1",
          color: "text.primary",
          fontWeight: "fontWeightRegular",
        }}
        startIcon={<SvgColor src="/assets/icons/status/success.svg" />}
      >
        Resolve issues
      </Button>
      <Divider
        orientation="vertical"
        sx={{
          borderColor: "divider",
          height: "80%",
        }}
      />
      <Button onClick={handleCancelSelection} size="small" variant="text">
        Cancel
      </Button>
    </Stack>
  );
};

export default function Issues() {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const {
    columnDefs,
    gridRef,
    selectedRows,
    setColumns,
    setSelectedAll,
    setExcludingIds,
    handleClearSelection,
    selectedAll,
    setSelectedRows,
    totalRows,
    setTotalRows,
    searchQuery,
    onSearchQueryChange,
    setAlertRuleDetails,
    setGridRef,
  } = useAlertSheetView();
  const { openSheetView, handleProjectChange } = useAlertStore();
  const {
    showFilterSection: showFilter,
    activeFilters,
    hasValidFilters,
  } = useAlertSheetFilterShallow();

  const timeoutRef = useRef(null);
  const agGridRef = useRef();

  useEffect(() => {
    if (agGridRef.current) {
      setGridRef(agGridRef);
    }
  }, [setGridRef]);

  const debouncedSearchTerm = useDebounce(searchQuery, 300);

  const extractedFilterObject = useMemo(() => {
    if (!hasValidFilters) return null;

    const filterObj = activeFilters.reduce((acc, filter) => {
      const { filterType, filterValue } = filter;

      if (Array.isArray(filterValue) && filterValue.length > 0) {
        acc[filterType] = filterValue;
      } else if (typeof filterValue === "string" && filterValue.trim() !== "") {
        acc[filterType] = filterValue;
      }

      return acc;
    }, {});

    return Object.keys(filterObj).length > 0 ? filterObj : null;
  }, [activeFilters, hasValidFilters]);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: false,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
    }),
    [],
  );

  const gridOptions = {
    pagination: false,
    rowSelection: { mode: "multiRow", enableClickSelection: false },
    suppressMultiSort: true,
  };

  const refreshRowsManual = useCallback(async () => {
    const totalPages = Object.keys(
      gridRef?.current?.api?.getCacheBlockState(),
    ).length;

    const sortModel =
      gridRef?.current?.api
        ?.getColumnState()
        ?.filter((c) => c?.sort != null)
        ?.map(({ colId, sort }) => ({
          sort_by: colId,
          sort_direction: sort,
        }))?.[0] || {};

    const { sort_by, sort_direction } = sortModel;

    for (let p = 0; p < totalPages; p++) {
      try {
        const rawParams = {
          page_number: p,
          search_text: debouncedSearchTerm,
          sort_by: sort_by || undefined,
          sort_direction,
          ...extractedFilterObject,
        };
        const { data } = await axios.get(
          endpoints.project.getAlertDetails(openSheetView),
          {
            params: rawParams,
          },
        );

        const rows = data?.result?.table ?? [];

        const transaction = {
          update: rows,
        };

        if (gridRef?.current?.api) {
          gridRef?.current?.api?.applyServerSideTransaction(transaction);
        }
      } catch (e) {
        logger.error("Failed to refresh rows", e);
      }
    }
  }, [debouncedSearchTerm, extractedFilterObject, gridRef, openSheetView]);

  // Server-side dataSource
  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        if (!openSheetView) return;
        const { request } = params;
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);
        const sortModel = request?.sortModel?.[0] || {};
        const { colId: sort_by, sort: sort_direction } = sortModel;

        const rawParams = {
          page_number: pageNumber,
          page_size: pageSize,
          search_text: debouncedSearchTerm,
          sort_by: sort_by || undefined,
          sort_direction,
          ...extractedFilterObject,
        };

        try {
          const { data } = await axios.get(
            endpoints.project.getAlertDetails(openSheetView),
            {
              params: rawParams,
            },
          );
          const rows = data?.result?.logs?.results ?? [];

          //for column filtering and rearranging;
          setColumns(issueColumns);
          setAlertRuleDetails({
            ...data.result,
            name: data?.result?.name,
            createdBy: data?.result?.created_by?.name,
            createdAt: data.result?.created_at,
            lastTriggered:
              data?.result?.last_triggered_at ?? data?.result?.last_checked_at,
            metricType: data?.result?.metric_type,
            metricName: data?.result?.metric_name,
            thresholdOperator: data?.result?.threshold_operator,
            criticalThresholdValue: data?.result?.critical_threshold_value,
            warningThresholdValue: data?.result?.warning_threshold_value,
            notificationEmails: data?.result?.notification_emails,
            isMute: data?.result?.is_mute,
          });

          if (data?.result?.project) {
            handleProjectChange(data.result.project);
          }

          params.success({
            rowData: rows,
            rowCount: data?.result?.logs?.metadata?.total_rows,
          });

          setTotalRows(data?.result?.logs?.metadata.total_rows ?? 0);

          const displayedNodes = [];
          params.api.forEachNode((node) => {
            if (node.displayed) {
              displayedNodes.push(node);
            }
          });
        } catch (error) {
          params.fail();
        }
      },
      getRowId: (data) => data.id,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [extractedFilterObject, debouncedSearchTerm, openSheetView],
  );

  const onSelectionChanged = useCallback(
    (event) => {
      if (event?.source === "apiSelectAll") {
        handleClearSelection();
        return;
      }

      if (!event) {
        if (timeoutRef?.current) {
          clearTimeout(timeoutRef.current);
        }
        timeoutRef.current = setTimeout(() => {
          setSelectedAll(false);
          setExcludingIds(new Set());
          handleClearSelection();
          gridRef?.current?.api?.deselectAll();
        }, 300);
        return;
      }

      const rowId = event.data?.id;

      if (selectedAll) {
        handleClearSelection();
        setExcludingIds((prev) => {
          const next = new Set(prev);
          if (!event?.node?.isSelected()) {
            next.add(rowId);
          } else {
            next.delete(rowId);
          }
          return next;
        });
      } else {
        setExcludingIds(new Set());
        const updatedSelectedRowsData = [...selectedRows];

        const rowIndex = updatedSelectedRowsData.findIndex(
          (row) => row?.id === rowId,
        );

        if (rowIndex === -1) {
          updatedSelectedRowsData.push(event.data);
        } else {
          updatedSelectedRowsData.splice(rowIndex, 1);
        }
        setSelectedRows(updatedSelectedRowsData);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      selectedAll,
      selectedRows,
      handleClearSelection,
      setExcludingIds,
      setSelectedRows,
    ],
  );

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
          ({ colId }) =>
            colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN &&
            colId !== "actions",
        );

      // Update columns with the new order based on newColumnOrder
      setColumns((prevColumns) => {
        // Create a new array with columns reordered based on newColumnOrder
        const reorderedColumns = newColumnOrder
          .map(({ colId }) => {
            const existingColumn = prevColumns.find(
              (col) => camelCase(col?.id) === colId,
            );
            return existingColumn || null;
          })
          .filter(Boolean);

        // Add any columns that might not be in the new order
        const remainingColumns = prevColumns.filter(
          (col) =>
            !newColumnOrder.some(({ colId }) => colId === camelCase(col.id)),
        );

        return [...reorderedColumns, ...remainingColumns];
      });
    },
    [setColumns],
  );

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      if (openSheetView) {
        refreshRowsManual();
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [openSheetView, refreshRowsManual]);

  return (
    <Stack gap={2}>
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <FormSearchField
          size="small"
          placeholder="Search"
          sx={{ minWidth: "360px" }}
          searchQuery={searchQuery}
          onChange={(e) => onSearchQueryChange(e.target.value)}
        />
        {selectedAll || selectedRows?.length > 0 ? (
          <RowActions />
        ) : (
          <TableAction />
        )}
      </Stack>
      <ShowComponent condition={showFilter}>
        <SheetFilters />
      </ShowComponent>
      <Box className="ag-theme-quartz" sx={{ height: "calc(100vh - 215px)" }}>
        <AgGridReact
          ref={agGridRef}
          theme={agTheme}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          {...gridOptions}
          serverSideInitialRowCount={10}
          rowModelType="serverSide"
          serverSideStoreType="partial"
          suppressContextMenu={true}
          cacheBlockSize={10}
          serverSideDatasource={dataSource}
          suppressServerSideFullWidthLoadingRow={true}
          onRowSelected={onSelectionChanged}
          getRowId={({ data }) => data?.id}
          onCellClicked={(event) => {
            if (
              event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              const selected = event.node.isSelected();
              event.node.setSelected(!selected);
            }
          }}
          onHeaderCellClicked={(event) => {
            if (event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
              const displayedNodes = [];
              event.api.forEachNode((node) => {
                if (node.displayed) {
                  displayedNodes.push(node);
                }
              });
              const allSelected = displayedNodes.every((node) =>
                node.isSelected(),
              );

              if (allSelected) {
                event.api.deselectAll();
              } else {
                event?.api.selectAll();
              }
            }
          }}
          onColumnHeaderClicked={(event) => {
            if (event?.column?.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
              return;

            if (totalRows <= 20) {
              const displayedNodes = [];
              event.api.forEachNode((node) => {
                if (node.displayed) {
                  displayedNodes.push(node);
                }
              });

              const allSelected = displayedNodes.every((node) =>
                node.isSelected(),
              );

              if (!allSelected) {
                event.api.deselectAll();
                handleClearSelection();
              } else {
                event.api.selectAll();
                const selectedNodes = [];
                event.api.forEachNode((node) => {
                  if (node.isSelected()) {
                    selectedNodes.push(node.data);
                  }
                });
                setSelectedRows(selectedNodes);
              }
            } else {
              if (selectedAll) {
                setSelectedAll(false);
                event.api.deselectAll();
                setExcludingIds(new Set());
                handleClearSelection();
              } else {
                setSelectedAll(true);
                event.api.selectAll();
                setExcludingIds(new Set());
                handleClearSelection();
              }
            }
          }}
          onColumnMoved={onColumnChanged}
        />
      </Box>
    </Stack>
  );
}
