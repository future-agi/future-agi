import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/utils/ag-grid-setup";
import React, { useMemo, useRef, useCallback, useEffect } from "react";
import { useNavigate } from "react-router";
import { useAgTheme } from "src/hooks/use-ag-theme";
import { getSimulationExecutionsColDef } from "./common";
import { useSimulationDetailContext } from "./context/SimulationDetailContext";
import { APP_CONSTANTS } from "src/utils/constants";
import {
  useSimulationExecutionsGridStore,
  resetSimulationDetailState,
} from "./states";
import {
  useExecutionGridDataSource,
  useDebouncedCellClick,
  createRowSelectionHandler,
  DEFAULT_COLUMN_DEF,
} from "src/sections/common/simulation";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";

const SimulationExecutionsGrid = () => {
  const agTheme = useAgTheme();
  const defaultColDef = useMemo(() => DEFAULT_COLUMN_DEF, []);

  const { simulation, setGridApi, searchQuery } = useSimulationDetailContext();
  const simulationId = simulation?.id;

  const columnDefs = useMemo(() => getSimulationExecutionsColDef(), []);
  const gridRef = useRef(null);

  const { setTotalRowCount } = useSimulationExecutionsGridStore();

  useEffect(() => {
    return () => {
      resetSimulationDetailState();
    };
  }, []);

  const navigate = useNavigate();

  const { dataSource, debouncedSearchQuery } = useExecutionGridDataSource({
    entityId: simulationId,
    queryKey: "simulation-executions-grid",
    searchQuery,
    onTotalRowCountChange: setTotalRowCount,
    gridRef,
  });

  const debounceCellClick = useDebouncedCellClick();

  const handleCellClick = useCallback(
    (params) => {
      navigate(`/dashboard/simulate/test/${simulationId}/${params.data.id}`);
    },
    [navigate, simulationId],
  );

  const onRowSelectionChanged = useMemo(
    () =>
      createRowSelectionHandler((state) =>
        useSimulationExecutionsGridStore.setState(state),
      ),
    [],
  );

  if (!simulationId) {
    return null;
  }

  return (
    <Box className="ag-theme-quartz" style={{ height: "100%" }}>
      <AgGridReact
        ref={gridRef}
        theme={agTheme}
        onGridReady={(params) => {
          setGridApi(params.api);
        }}
        rowSelection={{
          mode: "multiRow",
        }}
        onSelectionChanged={onRowSelectionChanged}
        columnDefs={columnDefs}
        defaultColDef={defaultColDef}
        pagination={true}
        paginationPageSize={20}
        suppressRowClickSelection={true}
        paginationPageSizeSelector={false}
        rowModelType="serverSide"
        suppressServerSideFullWidthLoadingRow={true}
        serverSideDatasource={dataSource}
        maxBlocksInCache={1}
        cacheBlockSize={20}
        rowStyle={{ cursor: "pointer" }}
        getRowId={(params) => params.data?.id}
        onFirstDataRendered={({ api }) => {
          const columns = api.getColumnDefs();
          const colNames = columns.reduce((acc, column) => {
            if (column.field && column.field !== "scenarios") {
              acc.push(column.field);
            }
            return acc;
          }, []);
          api.autoSizeColumns(colNames);
        }}
        noRowsOverlayComponent={() =>
          debouncedSearchQuery === "" ? (
            <EmptyLayout
              title="No runs yet"
              description="Run a simulation to see results here"
              icon="/assets/icons/navbar/ic_get_started.svg"
            />
          ) : (
            <EmptyLayout
              title="No results found"
              description="No runs match your search"
              hideIcon
            />
          )
        }
        onCellClicked={(params) => {
          const colId = params?.column?.getColId();
          if (colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
            const selected = params.node.isSelected();
            params.node.setSelected(!selected);
            return;
          }
          // Check if click was on a button (e.g., Stop button in status column)
          const target = params.event?.target;
          if (target?.closest("button")) {
            return;
          }
          debounceCellClick(handleCellClick, params);
        }}
      />
    </Box>
  );
};

export default SimulationExecutionsGrid;
