// MetricsContent.jsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import "src/styles/clean-data-table.css";
import { Box, Skeleton } from "@mui/material";
import MetricsHeaderSection from "../MetricsHeaderSection";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { getMetricsListColumnDefs, normalizeFilters } from "../common";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { useWorkbenchMetrics } from "../context/WorkbenchMetricsContext";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import {
  AllowedGroups,
  applyQuickFilters,
  mergeCellStyle,
} from "src/sections/projects/LLMTracing/common";
import { getRandomId } from "src/utils/utils";
import CustomTraceGroupHeaderRenderer from "src/sections/projects/LLMTracing/Renderers/CustomTraceGroupHeaderRenderer";
import MetricEmptyState from "../MetricEmptyState";

const LoadingHeader = () => {
  return <Skeleton variant="text" width={100} height={20} />;
};

const MetricsContent = () => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.borderless);
  const gridApiRef = useRef(null);
  const { columns, setColumns, filters, setFilters, setIsFilterDrawerOpen } =
    useWorkbenchMetrics();
  const [openQuickFilter, setOpenQuickFilter] = useState(null);
  const [hasData, setHasData] = useState(true); // Track if there's data
  const [isLoading, setIsLoading] = useState(false);
  const [hasInitialLoad, setHasInitialLoad] = useState(false);
  const [showEmptyState, setShowEmptyState] = useState(false);
  const { id } = useParams();

  const hasActiveFiltersOrSearch = useMemo(() => {
    const hasFilters = filters?.some((f) => f.columnId !== "");
    return hasFilters;
  }, [filters]);

  const defaultColDef = {
    filter: false,
    resizable: true,
    flex: 1,
    minWidth: 200,
    cellStyle: {
      padding: 0,
      height: "100%",
      display: "flex",
      flex: 1,
      flexDirection: "column",
    },
    suppressMovable: true,
    sortable: false,
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
            return getMetricsListColumnDefs(c, "evaluation");
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
              const colDef = getMetricsListColumnDefs(c, "evaluation");
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
          const validFilters = filters?.filter((f) => f.columnId !== "");
          // --- API Request ---
          const response = await axios.get(
            endpoints.develop.runPrompt.getPromptMetrics(),
            {
              params: {
                prompt_template_id: id,
                ...(validFilters?.length
                  ? { filters: JSON.stringify(normalizeFilters(filters)) }
                  : {}),
              },
            },
          );

          const res = response?.data?.result || {};

          const cols = res?.config?.map((o) => ({
            ...o,
          }));
          setColumns(cols);

          const rowData = res?.table || [];
          const totalRows = res?.metadata?.total_rows || 0;
          setHasData(totalRows > 0);
          setHasInitialLoad(true);

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

      getRowId: ({ data }) => data.promptVersionId,
    }),
    [id, filters, setColumns],
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
          paginationAutoPageSize={true}
          defaultColDef={defaultColDef}
          suppressRowClickSelection={true}
          rowStyle={{ cursor: "pointer" }}
          suppressSizeToFit={true}
          cacheBlockSize={10}
          suppressAutoSize={true}
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={5}
          animateRows={true}
          getMainMenuItems={(params) =>
            params.defaultItems.filter((item) => item !== "columnChooser")
          }
          headerHeight={40}
          groupHeaderHeight={40}
          overlayNoRowsTemplate={"No metrics found."}
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

export default MetricsContent;
