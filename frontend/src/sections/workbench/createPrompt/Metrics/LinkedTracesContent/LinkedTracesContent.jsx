// LinkedTracesContent.jsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import "src/styles/clean-data-table.css";
import { Box, Skeleton } from "@mui/material";
import MetricsHeaderSection from "../MetricsHeaderSection";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { getMetricsListColumnDefs, normalizeFilters } from "../common";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useWorkbenchMetrics } from "../context/WorkbenchMetricsContext";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import { useDebounce } from "src/hooks/use-debounce";
import CustomTraceGroupHeaderRenderer from "src/sections/projects/LLMTracing/Renderers/CustomTraceGroupHeaderRenderer";
import {
  AllowedGroups,
  applyQuickFilters,
  mergeCellStyle,
} from "src/sections/projects/LLMTracing/common";
import { getRandomId } from "src/utils/utils";
import MetricEmptyState from "../MetricEmptyState";

const LoadingHeader = () => {
  return <Skeleton variant="text" width={100} height={20} />;
};

const LinkedTracesContent = () => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.borderless);
  const gridApiRef = useRef(null);
  const {
    searchQuery,
    columns,
    setColumns,
    filters,
    setFilters,
    setIsFilterDrawerOpen,
  } = useWorkbenchMetrics();
  const [openQuickFilter, setOpenQuickFilter] = useState(null);
  const { id } = useParams();
  const [hasData, setHasData] = useState(true); // Track if there's data
  const [isLoading, setIsLoading] = useState(false);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [hasInitialLoad, setHasInitialLoad] = useState(false);
  const [showEmptyState, setShowEmptyState] = useState(false);

  const hasActiveFiltersOrSearch = useMemo(() => {
    const hasFilters = filters?.some((f) => f.columnId !== "");
    const hasSearch = debouncedSearchQuery?.length > 0;
    return hasFilters || hasSearch;
  }, [filters, debouncedSearchQuery]);

  const defaultColDef = {
    filter: false,
    resizable: true,
    flex: 1,
    suppressMovable: true,
    minWidth: 200,
    sortable: false,
    cellStyle: {
      padding: 0,
      height: "100%",
      display: "flex",
      flex: 1,
      flexDirection: "column",
    },
    cellRendererParams: {
      applyQuickFilters: applyQuickFilters(
        setFilters,
        setOpenQuickFilter,
        setIsFilterDrawerOpen,
      ),
    },
  };

  const { columnDefs } = useMemo(() => {
    // If columns are empty → return initial/default columnDefs
    if (!columns || columns.length === 0) {
      return {
        columnDefs: [
          {
            headerComponent: LoadingHeader,
            field: "traceId",
            flex: 1,
          },
          {
            headerComponent: LoadingHeader,
            field: "startTime",
            flex: 1,
          },
          {
            headerComponent: LoadingHeader,
            field: "duration",
            flex: 1,
          },
          {
            headerComponent: LoadingHeader,
            field: "status",
            flex: 1,
          },
          {
            headerComponent: LoadingHeader,
            field: "status",
            flex: 1,
          },
        ],
        bottomRow: [],
      };
    }

    // Normal logic when columns have data
    const grouping = {};
    const bottomRowObj = {};

    for (const eachCol of columns) {
      if (eachCol?.groupBy) {
        if (!grouping[eachCol?.groupBy]) {
          grouping[eachCol?.groupBy] = [eachCol];
        } else {
          grouping[eachCol?.groupBy].push(eachCol);
        }
      } else {
        grouping[getRandomId()] = [eachCol];
      }
    }

    const columnDefsResult = Object.entries(grouping).flatMap(
      ([group, cols]) => {
        if (group === "Annotation Metrics") {
          return cols.map((c) => {
            bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
            return getMetricsListColumnDefs(c);
          });
        }
        if (!AllowedGroups.includes(group) && cols.length === 1) {
          const c = cols[0];
          bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
          return getMetricsListColumnDefs(c);
        } else {
          return {
            headerName: group,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              const colDef = getMetricsListColumnDefs(c);
              return {
                ...colDef,
                minWidth: 200,
                flex: 1,
                cellStyle: mergeCellStyle(colDef, { paddingInline: 0 }),
              };
            }),
            headerGroupComponent: CustomTraceGroupHeaderRenderer,
          };
        }
      },
    );

    return {
      columnDefs: columnDefsResult,
      bottomRow: [
        {
          ...bottomRowObj,
        },
      ],
    };
  }, [columns]);

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        try {
          setIsLoading(true);

          // If filter is name, change it to spanName
          const validFilters = filters?.reduce((acc, f) => {
            if (f.columnId === "") return acc;
            acc.push(
              f.columnId === "name" ? { ...f, columnId: "spanName" } : f,
            );
            return acc;
          }, []);

          // --- API Request ---
          const response = await axios.get(
            endpoints.develop.runPrompt.getPromptSpanMetrics(),
            {
              params: {
                prompt_template_id: id,
                search_term: debouncedSearchQuery,
                ...(validFilters?.length
                  ? { filters: JSON.stringify(normalizeFilters(validFilters)) }
                  : {}),
              },
            },
          );

          // --- Response Handling ---
          const res = response?.data?.result || {};
          const cols = res?.config?.map((o) => ({
            ...o,
          }));
          setColumns(cols);
          // Return data to AG Grid
          const rowData = res?.table || [];
          const totalRows = res?.metadata?.total_rows || 0;

          // Update hasData based on response
          setHasData(totalRows > 0);
          setHasInitialLoad(true);
          // Return data to AG Grid
          params.success({
            rowData,
            rowCount: totalRows,
          });
        } catch (error) {
          setHasData(false);
          setHasInitialLoad(true);
          params.success({
            rowData: [],
            rowCount: 0,
          });
        } finally {
          setIsLoading(false);
        }
      },

      // --- Row Identification ---
      getRowId: ({ data }) => data.spanId,
    }),
    [id, filters, debouncedSearchQuery, setColumns],
  );

  useEffect(() => {
    if (!isLoading && !hasData && !hasActiveFiltersOrSearch && hasInitialLoad) {
      const timer = setTimeout(() => {
        setShowEmptyState(true);
      }, 100);
      return () => clearTimeout(timer);
    } else {
      setShowEmptyState(false);
    }
  }, [isLoading, hasData, hasActiveFiltersOrSearch, hasInitialLoad]);

  if (showEmptyState) {
    return <MetricEmptyState />;
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <MetricsHeaderSection />
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <AgGridReact
          className="clean-data-table"
          ref={(params) => {
            gridApiRef.current = params;
          }}
          columnDefs={columnDefs}
          rowModelType="serverSide"
          serverSideDatasource={dataSource}
          rowHeight={40}
          theme={agTheme}
          rowSelection="single"
          paginationPageSizeSelector={false}
          pagination={true}
          cacheBlockSize={10}
          paginationAutoPageSize={true}
          defaultColDef={defaultColDef}
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={5}
          suppressRowClickSelection={true}
          rowStyle={{ cursor: "pointer" }}
          suppressSizeToFit={true}
          suppressAutoSize={true}
          animateRows={true}
          headerHeight={40}
          groupHeaderHeight={40}
          tooltipShowDelay={0}
          tooltipHideDelay={2000}
          tooltipInteraction={true}
        />
      </Box>
      <NumberQuickFilterPopover
        open={Boolean(openQuickFilter)}
        filterData={openQuickFilter}
        onClose={() => setOpenQuickFilter(null)}
        setFilters={setFilters}
        setFilterOpen={setIsFilterDrawerOpen}
      />
    </Box>
  );
};

export default LinkedTracesContent;
