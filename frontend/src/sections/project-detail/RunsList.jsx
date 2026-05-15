import { AgGridReact } from "ag-grid-react";
import React, { useMemo, useRef, useState } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { getRandomId, preventHeaderSelection } from "src/utils/utils";
import PropTypes from "prop-types";
import { useNavigate, useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import { objectCamelToSnake } from "src/utils/utils";
import TotalRowsStatusBar from "../develop-detail/Common/TotalRowsStatusBar";
import { APP_CONSTANTS } from "src/utils/constants";

import {
  AllowedGroups,
  applyQuickFilters,
  getRunListColumnDefs,
} from "./common";

const RunsList = React.forwardRef(
  (
    {
      columns,
      setColumns,
      search,
      filters,
      setFilters,
      setSelectedRowsData,
      setFilterOpen,
    },
    ref,
  ) => {
    const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.withColumnBorder);
    const { projectId } = useParams();

    const gridRef = useRef(null);
    const navigate = useNavigate();
    const [selectedAll, setSelectedAll] = useState(false);
    preventHeaderSelection();

    const [openQuickFilter, setOpenQuickFilter] = useState(null);

    const defaultColDef = {
      filter: false,
      resizable: true,
      flex: 1,
      suppressMovable: true,
      sortable: true,
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

    const [statusBar] = useState({
      statusPanels: [
        {
          statusPanel: TotalRowsStatusBar,
          align: "left",
        },
      ],
    });

    const { columnDefs } = useMemo(() => {
      // Case 1: If no columns fetched yet → Return initial default columnDefs
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
          ],
          bottomRow: [],
        };
      }
      const grouping = {};

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

      const columnDefsResult = Object.entries(grouping)
        .flatMap(([group, cols]) => {
          if (group === "Annotation Metrics") {
            return cols.map((c) => getRunListColumnDefs(c));
          }
          if (!AllowedGroups.includes(group) && cols.length === 1) {
            const c = cols[0];
            // bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
            return getRunListColumnDefs(c);
          } else {
            return {
              headerName: group,
              children: cols.map((c) => {
                // bottomRowObj[c?.id] = c?.average
                //   ? `Average ${c?.average}`
                //   : null;
                return getRunListColumnDefs(c);
              }),
            };
          }
        })
        .sort((a, b) => {
          if (a.field === "rank") {
            return -1;
          }
          if (b.field === "rank") {
            return 1;
          }
          return 0;
        });

      return {
        columnDefs: columnDefsResult,
        // bottomRow: [
        //   {
        //     ...bottomRowObj,
        //   },
        // ],
      };
    }, [columns]);

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          try {
            const { request } = params;
            setSelectedAll(false);

            // request has startRow and endRow get next page number and each page has 10 rows
            const pageNumber = Math.floor(request.startRow / 10);

            const fil = [...filters];

            if (search && search?.length) {
              fil.push({
                columnId: "run_name",
                filterConfig: {
                  filterOp: "contains",
                  filterType: "text",
                  filterValue: search,
                },
              });
            }

            const results = await axios.get(
              endpoints.project.projectExperimentRun(),
              {
                params: {
                  project_id: projectId,
                  page_number: pageNumber,
                  page_size: 30,
                  sort_params: JSON.stringify(
                    request?.sortModel?.map(({ colId, sort }) => ({
                      column_id: colId,
                      direction: sort,
                    })),
                  ),
                  filters: JSON.stringify(objectCamelToSnake(fil)),
                },
              },
            );
            const res = results?.data?.result;

            const winnerConfig = res?.project_version_winnner_config || {};
            const columns = res?.column_config?.map((o) => {
              if (o.id === "avg_latency") {
                return {
                  ...o,
                  value: winnerConfig["avg_latency_ms"] ?? null,
                };
              }

              return {
                ...o,
                value: winnerConfig[o.id] ?? null,
              };
            });

            setColumns(columns);

            params.success({
              rowData: res?.table,
              totalRows: res?.metadata?.total_rows,
            });
          } catch (error) {
            params.fail();
          }
        },
        getRowId: ({ data }) => {
          return data.row_id;
        },
      }),
      [filters, search, projectId, setColumns],
    );

    const onSelectionChanged = (event) => {
      if (!event) {
        setTimeout(() => {
          setSelectedRowsData([]);
        }, 300);
        gridRef?.current?.api?.deselectAll();
        return;
      }

      const rowId = event.data.id;

      setSelectedRowsData((prevSelectedItems) => {
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

      // Tracking event
      trackEvent(Events.pExperimentRunSelected, {
        [PropertyName.click]: event.data,
      });
    };

    return (
      <>
        <AgGridReact
          rowSelection={{ mode: "multiRow" }}
          ref={(params) => {
            gridRef.current = params;
            ref.current = params;
          }}
          theme={agTheme}
          onColumnHeaderClicked={(event) => {
            if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
              return;
            }

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
          pagination={false}
          cacheBlockSize={30}
          suppressRowClickSelection={true}
          rowModelType="serverSide"
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={5}
          serverSideDatasource={dataSource}
          onRowSelected={onSelectionChanged}
          onCellClicked={(event) => {
            if (
              event.column.getColId() !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              const { data } = event;
              navigate(`/dashboard/prototype/${projectId}/${data.id}`, {
                state: { dataset: data },
              });
            } else {
              const selected = event.node.isSelected();
              event.node.setSelected(!selected);
            }
          }}
          statusBar={statusBar}
        />
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

RunsList.displayName = "RunsList";

RunsList.propTypes = {
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  search: PropTypes.string,
  onGridReady: PropTypes.func,
  filters: PropTypes.array,
  setFilters: PropTypes.func,
  setSelectedRowsData: PropTypes.func,
  setFilterOpen: PropTypes.func,
};

export default RunsList;
