import { Box, Collapse } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import React, { useMemo, useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { getRandomId } from "src/utils/utils";

import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import {
  AllowedGroups,
  applyQuickFilters,
  generateSpanFilterDefinition,
  getSpanListColumnDefs,
} from "../traces-tab/common";
import { useDebounce } from "src/hooks/use-debounce";
import ComplexFilter from "src/components/ComplexFilter/ComplexFilter";
import { Events, trackEvent } from "src/utils/Mixpanel";
import useReverseEvalFilters from "src/hooks/use-reverse-eval-filters";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import { getFilterExtraProperties } from "../../../utils/prototypeObserveUtils";
import TotalRowsStatusBar from "src/sections/develop-detail/Common/TotalRowsStatusBar";
import { useQuery } from "@tanstack/react-query";
import { objectCamelToSnake } from "src/utils/utils";
import { canonicalizeApiFilterColumnIds } from "src/utils/filter-column-ids";
import { generateAnnotationColumnsForTracing } from "src/sections/projects/LLMTracing/common";
import { useShallowToggleAnnotationsStore } from "src/sections/agents/store";

const defaultFilter = {
  columnId: "",
  filterConfig: {
    filterType: "",
    filterOp: "",
    filterValue: "",
  },
};

const SpanTab = React.forwardRef(
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

    const [statusBar] = useState({
      statusPanels: [
        {
          statusPanel: TotalRowsStatusBar,
          align: "left",
        },
      ],
    });
    const { showMetricsIds, reset: resetMetricIds } =
      useShallowToggleAnnotationsStore((state) => ({
        showMetricsIds: state.showMetricsIds,
        reset: state.reset,
      }));
    const [filters, setFilters] = useState([
      { ...defaultFilter, id: getRandomId() },
    ]);

    const { data: evalAttributes } = useQuery({
      queryKey: ["eval-attributes", projectId],
      queryFn: () =>
        axios.get(endpoints.project.getEvalAttributeList(), {
          params: {
            filters: JSON.stringify({ project_id: projectId }),
          },
        }),
      select: (data) => data.data?.result,
    });

    const [filterDefinition, setFilterDefinition] = useState(() => {
      return generateSpanFilterDefinition(columns, evalAttributes, filters);
    });

    // Memoized helper for preserving attribute definitions
    const preserveAttributeDefinitions = useMemo(() => {
      return (prevDefinition, newBaseDefinition) => {
        const attributionIndex = prevDefinition?.findIndex(
          (item) => item?.propertyName === "Attribute",
        );

        if (prevDefinition?.[attributionIndex]?.dependents?.length > 0) {
          // Already has the Attribute block — preserve it
          const copy = [...newBaseDefinition];
          const copyAttributionIndex = copy?.findIndex(
            (item) => item?.propertyName === "Attribute",
          );
          if (copyAttributionIndex >= 0) {
            copy[copyAttributionIndex] = prevDefinition[attributionIndex];
          }
          return copy;
        } else {
          // Generate fresh with attributes
          return newBaseDefinition;
        }
      };
    }, []);

    useEffect(() => {
      setFilterDefinition((prevDefinition) => {
        const newBaseDefinition = generateSpanFilterDefinition(
          columns,
          evalAttributes,
          filters,
        );
        return preserveAttributeDefinitions(prevDefinition, newBaseDefinition);
      });
    }, [columns, evalAttributes, filters, preserveAttributeDefinitions]);

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
        f.filterConfig?.filterValue && Array.isArray(f.filterConfig.filterValue)
          ? f.filterConfig.filterValue.length > 0
          : f.filterConfig.filterValue !== "",
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
      sortable: false,
      minWidth: 200,
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
      // If no columns yet → return initial columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: [
            {
              headerName: "Column 1",
              field: "operation_name",
              flex: 1,
            },
            {
              headerName: "Column 2",
              field: "start_time",
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

      // If columns are populated → process normally
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
          return getSpanListColumnDefs(c);
        } else {
          return {
            headerName: group,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              return getSpanListColumnDefs(c);
            }),
          };
        }
      });
      if (annotationColumns.length > 0) {
        columnDefsResult.push(annotationColumns[0]);
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

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          try {
            const { request } = params;

            // request has startRow and endRow get next page number and each page has 10 rows
            const pageNumber = Math.floor(request.startRow / 10);

            const results = await axios.get(
              endpoints.project.getSpanList(),

              {
                params: {
                  filters: JSON.stringify(
                    canonicalizeApiFilterColumnIds(
                      objectCamelToSnake(debouncedValidatedFilters),
                    ),
                  ),
                  project: projectId,
                  project_version_id: runId,
                  page_number: pageNumber,
                  page_size: 10,
                  trace_ids: selectedTraceIds.join(","),
                },
              },
            );
            const res = results?.data?.result;
            const columns = res?.columnConfig?.map((o) => ({
              ...o,
              id: o.id,
            }));
            setColumns(columns);

            params.api.totalRowCount = res?.metadata?.totalRows;
            params.success({
              rowData: res?.table,
              totalRows: res?.metadata?.totalRows,
            });
          } catch (error) {
            params.fail();
          }
        },
        getRowId: ({ data }) => {
          return data.rowId;
        },
      }),
      [debouncedValidatedFilters, selectedTraceIds],
    );

    useEffect(() => {
      return () => resetMetricIds();
    }, []);

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
            />
          </Box>
        </Collapse>
        <Box
          className="ag-theme-quartz"
          style={{
            flex: 1,
            padding: "12px",
          }}
          sx={{ height: "100%" }}
        >
          {/* <RunInsightsFilterBox
            setDevelopFilterOpen={setDevelopFilterOpen}
            developFilterOpen={developFilterOpen}
            filters={filters}
            setFilters={setFilters}
            allColumns={allColumns}
          /> */}
          <Box
            className="ag-theme-quartz custom-grid"
            style={{ height: "100%", overflowX: "auto" }}
          >
            <AgGridReact
              ref={gridApiRef}
              className="clean-data-table"
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
                  spanId: event.data.span_id,
                  filters: debouncedValidatedFilters,
                  fromSpansView: true,
                });
              }}
              getRowId={({ data }) => {
                return data.span_id;
              }}
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
        </Box>
      </>
    );
  },
);

SpanTab.displayName = "SpanTab";

SpanTab.propTypes = {
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  setTraceDetailDrawerOpen: PropTypes.func,
  filterOpen: PropTypes.bool,
  selectedTraceIds: PropTypes.array,
  setFilterOpen: PropTypes.func,
  setIsFilterApplied: PropTypes.func,
};

export default SpanTab;
