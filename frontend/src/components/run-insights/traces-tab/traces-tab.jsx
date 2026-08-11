import { Box, Button, Collapse } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import React, { useMemo, useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { getRandomId } from "src/utils/utils";
import "./tracesTab.css";

import {
  AllowedGroups,
  applyQuickFilters,
  generateTraceFilterDefinition,
  getTraceListColumnDefs,
  statusBar,
} from "./common";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
import { useDebounce } from "src/hooks/use-debounce";
import { Events, trackEvent } from "src/utils/Mixpanel";
import useReverseEvalFilters from "src/hooks/use-reverse-eval-filters";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import { getFilterExtraProperties } from "../../../utils/prototypeObserveUtils";
import { generateAnnotationColumnsForTracing } from "src/sections/projects/LLMTracing/common";
import { useShallowToggleAnnotationsStore } from "src/sections/agents/store";
import { getListTotalState } from "src/sections/projects/LLMTracing/listTotalMetadata";
import { parsePrototypeTraceListResponse } from "src/api/project/telemetry-list-contract";
import { useRunInsightAttributeKeys } from "./useRunInsightAttributeKeys";

const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

const normalizeTraceListPayload = (payload) => {
  const normalized = parsePrototypeTraceListResponse(payload);
  const metadata = normalized.metadata;
  const totalState = getListTotalState(metadata);

  return {
    ...normalized,
    ...totalState,
  };
};

const TraceTab = React.forwardRef(
  (
    {
      columns,
      setColumns,
      setTraceDetailDrawerOpen,
      filterOpen,
      selectedTraceIds,
      setFilterOpen,
      setIsFilterApplied,
    },
    gridApiRef,
  ) => {
    const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.borderless);
    const { projectId, runId } = useParams();
    const [openQuickFilter, setOpenQuickFilter] = useState(null);

    const [filters, setFilters] = useState([
      { ...defaultFilter, id: getRandomId() },
    ]);

    const {
      attributeKeys: evalAttributes,
      hasNextPage: hasNextAttributePage,
      fetchNextPage: fetchNextAttributePage,
      isFetchingNextPage: isFetchingNextAttributePage,
    } = useRunInsightAttributeKeys(projectId);

    const [filterDefinition, setFilterDefinition] = useState(() => {
      return generateTraceFilterDefinition(columns, evalAttributes, filters);
    });

    // const filterDefinition = useMemo(
    //   () => generateTraceFilterDefinition(columns),
    //   [columns],
    // );
    const { showMetricsIds, reset: resetMetricIds } =
      useShallowToggleAnnotationsStore((state) => ({
        showMetricsIds: state.showMetricsIds,
        reset: state.reset,
      }));
    useEffect(() => {
      // Attribute pages are cumulative and de-duplicated. Rebuilding from all
      // loaded pages appends new dependents, while `filters` preserves the
      // selected attribute and its chosen scalar editor type.
      setFilterDefinition(
        generateTraceFilterDefinition(columns, evalAttributes, filters),
      );
    }, [columns, evalAttributes, filters]);

    const reversePrimaryEvalColumnIds = useMemo(() => {
      return columns.filter((c) => c?.reverseOutput).map((c) => c.id);
    }, [columns]);

    const validatedFilters = useReverseEvalFilters(
      filters,
      reversePrimaryEvalColumnIds,
      getFilterExtraProperties,
    );

    const debouncedValidatedFilters = useDebounce(validatedFilters, 500);

    useEffect(() => {
      const hasActiveFilter = debouncedValidatedFilters?.some((f) =>
        f.filter_config?.filter_value &&
        Array.isArray(f.filter_config.filter_value)
          ? f.filter_config.filter_value.length > 0
          : f.filter_config.filter_value !== "",
      );
      setIsFilterApplied(hasActiveFilter);
      trackEvent(Events.filterApplied);
    }, [debouncedValidatedFilters, setIsFilterApplied]);

    // Grid Options
    const defaultColDef = {
      filter: false,
      resizable: true,
      flex: 1,
      suppressMovable: true,
      minWidth: 200,
      sortable: false,
      cellStyle: {
        padding: 0,
      },
      cellRendererParams: {
        applyQuickFilters: applyQuickFilters(
          setFilters,
          setOpenQuickFilter,
          setFilterOpen,
        ),
      },
    };

    const { columnDefs } = useMemo(() => {
      // Case 1: If columns are empty, return default columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: [
            {
              headerName: "Column 1",
              field: "name",
              flex: 1,
            },
            {
              headerName: "Column 2",
              field: "trace_id",
              flex: 1,
            },
            {
              headerName: "Column 3",
              field: "duration",
              flex: 1,
            },
            {
              headerName: "Column 4",
              field: "status",
              flex: 1,
            },
            {
              headerName: "Column 5",
              field: "status",
              flex: 1,
            },
          ],
          bottomRow: [],
        };
      }

      // Case 2: Columns exist → proceed with grouping and dynamic defs
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

      const annotationColumns = generateAnnotationColumnsForTracing(
        grouping["Annotation Metrics"],
        showMetricsIds,
      );
      delete grouping["Annotation Metrics"];

      const columnDefsResult = Object.entries(grouping).map(([group, cols]) => {
        if (!AllowedGroups.includes(group) && cols.length === 1) {
          const c = cols[0];
          bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
          return getTraceListColumnDefs(c);
        } else {
          return {
            headerName: group,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              return getTraceListColumnDefs(c);
            }),
          };
        }
      });
      if (annotationColumns.length > 0) {
        for (const group of annotationColumns) {
          if (group.children) {
            columnDefsResult.push(...group.children);
          } else {
            columnDefsResult.push(group);
          }
        }
      }
      return {
        columnDefs: columnDefsResult,
        bottomRow: [
          {
            ...bottomRowObj,
          },
        ],
      };
    }, [columns, showMetricsIds]);
    useEffect(() => {
      return () => resetMetricIds();
    }, [resetMetricIds]);

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          try {
            const { request } = params;

            // request has startRow and endRow get next page number and each page has 10 rows
            const pageNumber = Math.floor(request.startRow / 10);

            const results = await axios.get(endpoints.project.getTraceList(), {
              params: {
                project_version_id: runId,
                page_number: pageNumber,
                trace_ids: selectedTraceIds.join(","),
                page_size: 10,
                filters: JSON.stringify(debouncedValidatedFilters),
              },
            });
            const res = normalizeTraceListPayload(results.data);
            const columns = res.columnConfig.map((o) => ({
              ...o,
              id: o.id,
            }));
            setColumns(columns);

            params.api.totalRowCount = res.totalRowCount;
            params.api.totalRowCountLowerBound = res.totalRowCountLowerBound;
            params.api.totalRowCountIsLowerBound =
              res.totalRowCountIsLowerBound;
            const successPayload = { rowData: res.table };
            if (!res.totalRowCountIsLowerBound) {
              successPayload.totalRows = res.totalRows;
            }
            params.success(successPayload);
          } catch (error) {
            params.fail();
          }
        },
        getRowId: ({ data }) => {
          return data.trace_id;
        },
      }),
      [debouncedValidatedFilters, runId, selectedTraceIds, setColumns],
    );

    return (
      <>
        <Collapse in={filterOpen}>
          <Box sx={{ paddingX: "12px", paddingTop: "16px" }}>
            <ComplexFilter
              filters={filters}
              defaultFilter={defaultFilter}
              setFilters={setFilters}
              filterDefinition={filterDefinition}
              onClose={() => setFilterOpen(false)}
              projectId={projectId}
            />
            {hasNextAttributePage && (
              <Box sx={{ display: "flex", justifyContent: "flex-end", mt: 1 }}>
                <Button
                  size="small"
                  disabled={isFetchingNextAttributePage}
                  onClick={() => fetchNextAttributePage()}
                >
                  {isFetchingNextAttributePage
                    ? "Loading attributes…"
                    : "Load more attributes"}
                </Button>
              </Box>
            )}
          </Box>
        </Collapse>
        <Box
          sx={{
            padding: "12px",
            flex: 1,
          }}
        >
          <AgGridReact
            ref={gridApiRef}
            theme={agTheme}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            pagination={false}
            cacheBlockSize={10}
            maxBlocksInCache={10}
            suppressRowClickSelection={true}
            rowModelType="serverSide"
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={10}
            serverSideDatasource={dataSource}
            onRowClicked={(event) => {
              setTraceDetailDrawerOpen({
                traceId: event.data.trace_id,
                filters: validatedFilters,
                data: event.data,
              });
            }}
            getRowId={({ data }) => {
              return data.trace_id;
            }}
            className="trace-tab-grid clean-data-table"
            statusBar={statusBar}
          />
        </Box>
        <NumberQuickFilterPopover
          open={Boolean(openQuickFilter)}
          filterData={openQuickFilter}
          onClose={() => setOpenQuickFilter(null)}
          setFilters={setFilters}
          setFilterOpen={setFilterOpen}
        />
      </>
    );
  },
);

TraceTab.displayName = "TraceTab";

TraceTab.propTypes = {
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  setTraceDetailDrawerOpen: PropTypes.func,
  filterOpen: PropTypes.bool,
  selectedTraceIds: PropTypes.array,
  setFilterOpen: PropTypes.func,
  setIsFilterApplied: PropTypes.func,
};

export default TraceTab;
