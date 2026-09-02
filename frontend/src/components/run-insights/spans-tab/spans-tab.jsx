import { Box, Button, Collapse } from "@mui/material";
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
import { generateAnnotationColumnsForTracing } from "src/sections/projects/LLMTracing/common";
import { useShallowToggleAnnotationsStore } from "src/sections/agents/store";
import { getListTotalState } from "src/sections/projects/LLMTracing/listTotalMetadata";
import { parsePrototypeSpanListResponse } from "src/api/project/telemetry-list-contract";
import { getSpanPhysicalRowId } from "src/sections/projects/LLMTracing/spanPhysicalIdentity";
import AttributeInventoryControls from "src/sections/projects/LLMTracing/AttributeInventoryControls";
import { useCursorAttributeInventory } from "src/sections/projects/LLMTracing/useCursorAttributeInventory";
import { QUERY_FAILED_RETRY_MESSAGE } from "src/utils/queryReadState";
import { readRunInsightListPage } from "../run_insight_list_read";
import {
  FILTER_VALUE_SEARCH_DEBOUNCE_MS,
  INTERACTIVE_TABLE_PAGE_SIZE,
} from "src/config/runtime_limits";

const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

const normalizeSpanListPayload = (payload) => {
  const normalized = parsePrototypeSpanListResponse(payload);
  const metadata = normalized.metadata;
  const totalState = getListTotalState(metadata);

  return {
    ...normalized,
    ...totalState,
  };
};

const SpanTab = React.forwardRef(
  (
    {
      columns,
      setColumns,
      setTraceDetailDrawerOpen,
      filterOpen,
      setFilterOpen,
      setIsFilterApplied,
    },
    gridApiRef,
  ) => {
    const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.borderless);
    const { projectId, runId } = useParams();
    const [openQuickFilter, setOpenQuickFilter] = useState(null);
    const [readError, setReadError] = useState(null);

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
    const [attributeSearch, setAttributeSearch] = useState("");
    const preservedAttributeKeys = useMemo(
      () =>
        filters.flatMap((filter) =>
          filter?._meta?.parentProperty === "Attribute" && filter?.column_id
            ? [filter.column_id]
            : [],
        ),
      [filters],
    );
    const { attributes: evalAttributes, inventoryControlProps } =
      useCursorAttributeInventory({
        projectId,
        discoveryMode: "filter",
        search: attributeSearch,
        preservedKeys: preservedAttributeKeys,
      });
    const filterDefinition = useMemo(
      () => generateSpanFilterDefinition(columns, evalAttributes, filters),
      [columns, evalAttributes, filters],
    );

    const reversePrimaryEvalColumnIds = useMemo(() => {
      return columns.filter((c) => c?.reverseOutput).map((c) => c.id);
    }, [columns]);

    const validatedFilters = useReverseEvalFilters(
      filters,
      reversePrimaryEvalColumnIds,
      getFilterExtraProperties,
    );

    const debouncedValidatedFilters = useDebounce(
      validatedFilters,
      FILTER_VALUE_SEARCH_DEBOUNCE_MS,
    );

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

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          try {
            const { request } = params;

            const pageNumber = Math.floor(
              request.startRow / INTERACTIVE_TABLE_PAGE_SIZE,
            );

            const results = await readRunInsightListPage(
              ({ signal, timeout }) =>
                axios.get(endpoints.project.getSpanList(), {
                  signal,
                  timeout,
                  params: {
                    filters: JSON.stringify(debouncedValidatedFilters),
                    project_version_id: runId,
                    page_number: pageNumber,
                    page_size: INTERACTIVE_TABLE_PAGE_SIZE,
                  },
                }),
            );
            const res = normalizeSpanListPayload(results.data);
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
            setReadError(null);
            params.success(successPayload);
          } catch {
            setReadError(QUERY_FAILED_RETRY_MESSAGE);
            params.fail();
          }
        },
        getRowId: ({ data }) => getSpanPhysicalRowId(data),
      }),
      [debouncedValidatedFilters, runId, setColumns],
    );

    useEffect(() => {
      return () => resetMetricIds();
    }, [resetMetricIds]);

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
              onAttributeSearchChange={setAttributeSearch}
            />
            <AttributeInventoryControls
              {...inventoryControlProps}
              showSearch={false}
              search={attributeSearch}
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
            {readError && (
              <Box
                role="alert"
                sx={{
                  px: 1.5,
                  py: 0.75,
                  color: "warning.main",
                  bgcolor: "warning.lighter",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                {readError}
                <Button
                  size="small"
                  onClick={() => {
                    setReadError(null);
                    gridApiRef?.current?.api?.refreshServerSide({
                      purge: false,
                    });
                  }}
                >
                  Retry
                </Button>
              </Box>
            )}
            <AgGridReact
              ref={gridApiRef}
              className="clean-data-table"
              theme={agTheme}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              pagination={false}
              cacheBlockSize={INTERACTIVE_TABLE_PAGE_SIZE}
              maxBlocksInCache={10}
              suppressRowClickSelection={true}
              rowModelType="serverSide"
              suppressServerSideFullWidthLoadingRow={true}
              serverSideInitialRowCount={INTERACTIVE_TABLE_PAGE_SIZE}
              serverSideDatasource={dataSource}
              onRowClicked={(event) => {
                setTraceDetailDrawerOpen({
                  traceId: event.data.trace_id,
                  spanId: event.data.span_id,
                  filters: debouncedValidatedFilters,
                  fromSpansView: true,
                });
              }}
              getRowId={({ data }) => getSpanPhysicalRowId(data)}
              statusBar={statusBar}
            />
          </Box>
          <NumberQuickFilterPopover
            open={Boolean(openQuickFilter)}
            filterData={openQuickFilter}
            onClose={() => setOpenQuickFilter(null)}
            setFilters={setFilters}
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
  setFilterOpen: PropTypes.func,
  setIsFilterApplied: PropTypes.func,
};

export default SpanTab;
