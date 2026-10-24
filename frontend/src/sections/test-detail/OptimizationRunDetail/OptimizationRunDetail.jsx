import { Box } from "@mui/material";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { useQueryClient } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo, useRef, useState, useCallback } from "react";
import { useNavigate, useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { getOptimizationRunDetailColumDef } from "./common";
import NoRowsOverlay from "./NoRowsOverlay";
import { useTestDetail } from "../context/TestDetailContext";
import OptimizationRerun from "./OptimizationRerun";

const rowStyle = { cursor: "pointer" };
const getRowId = (params) => params.data.id;

const OptimizationNoRowsOverlay = () => (
  <NoRowsOverlay title="No optimization runs found" />
);

const PAGE_SIZES = [10, 25, 50];

const OptimizationRunDetail = () => {
  const gridTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const [page, setPage] = useState(1);
  const [pageLimit, setPageLimit] = useState(10);
  const [totalRows, setTotalRows] = useState(0);
  const [, setTotalPages] = useState(1);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const gridRef = useRef(null);
  const { setOptimizationGridApi } = useTestDetail();
  const totalPagesRef = useRef(1);

  const { executionId, testId } = useParams();

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;

        const pageSize = pageLimit ?? request?.endRow - request?.startRow;
        const pageNumber = Math.floor(request?.startRow / pageSize) + 1;

        const queryParams = {
          page: pageNumber,
          page_size: pageSize,
          test_execution_id: executionId,
        };

        const queryKey = [
          "agent-optimization-runs",
          executionId,
          pageSize,
          pageNumber,
        ];

        try {
          const { data } = await queryClient.fetchQuery({
            queryKey,
            queryFn: () =>
              axios.get(endpoints.optimizeSimulate.getOptimizationRuns(), {
                params: queryParams,
              }),
          });

          const rows = data?.result?.table;
          const total = data?.result?.metadata?.total_rows;
          const totalPages = Math.ceil(total / pageSize);
          setTotalRows(total || 0);
          if (totalPages !== totalPagesRef.current) {
            totalPagesRef.current = totalPages;
            setTotalPages(totalPages);
          }

          if (rows?.length === 0) {
            params.api.showNoRowsOverlay();
          } else {
            params.api.hideOverlay();
          }

          params.success({
            rowData: rows,
            rowCount: total,
          });
        } catch (_) {
          params.fail();
          params.api.showNoRowsOverlay();
        }
      },
      getRowId: (data) => data.id,
    }),
    [queryClient, pageLimit, executionId],
  );

  const columnDefs = useMemo(() => {
    return getOptimizationRunDetailColumDef();
  }, []);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: false,
      filter: false,
      resizable: true,
      suppressMenu: true,
    }),
    [],
  );

  const onPaginationChanged = useCallback(() => {
    if (gridRef.current?.api) {
      const currentPage = gridRef.current.api.paginationGetCurrentPage();
      setPage(currentPage + 1);
    }
  }, []);

  const onGridReady = useCallback(
    (params) => {
      setOptimizationGridApi(params.api);
    },
    [setOptimizationGridApi],
  );

  const onCellClicked = useCallback(
    (params) => {
      const target = params.event?.target;

      if (target?.closest(".optimization-run-detail-refresh-button")) {
        return;
      }

      navigate(
        `/dashboard/simulate/test/${testId}/${executionId}/${params.data.id}`,
      );
    },
    [navigate, testId, executionId],
  );

  const handlePageChange = useCallback((event, value) => {
    if (gridRef.current?.api) {
      gridRef.current.api.paginationGoToPage(value - 1);
    }
  }, []);

  const handlePageLimitChange = useCallback((e) => {
    const newLimit = Number(e.target.value);
    setPageLimit(newLimit);
    setPage(1);

    if (gridRef.current?.api) {
      gridRef.current.api.paginationGoToPage(0);
      gridRef.current.api.refreshServerSide({ purge: true });
    }
  }, []);
  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        padding: 2,
        minHeight: 0,
      }}
    >
      <OptimizationRerun />
      <Box
        className="ag-theme-alpine"
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            height: "100%",
            "& .ag-root-wrapper": {
              height: "100% !important",
            },
            "& .ag-root": {
              height: "100% !important",
            },
            "& .ag-body": {
              height: "100% !important",
            },
            "& .ag-body-viewport": {
              height: "100% !important",
            },
          }}
        >
          <AgGridReact
            ref={gridRef}
            theme={gridTheme}
            onGridReady={onGridReady}
            rowHeight={44}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            suppressServerSideFullWidthLoadingRow={true}
            suppressRowClickSelection={true}
            pagination={true}
            paginationPageSize={pageLimit}
            paginationPageSizeSelector={false}
            rowModelType="serverSide"
            serverSideDatasource={dataSource}
            maxBlocksInCache={5}
            cacheBlockSize={pageLimit}
            serverSideInitialRowCount={pageLimit}
            rowStyle={rowStyle}
            suppressContextMenu
            onPaginationChanged={onPaginationChanged}
            suppressPaginationPanel={true}
            getRowId={getRowId}
            onCellClicked={onCellClicked}
            noRowsOverlayComponent={OptimizationNoRowsOverlay}
          />
        </Box>

        <DataTablePagination
          page={page - 1}
          pageSize={pageLimit}
          total={totalRows}
          pageSizeOptions={PAGE_SIZES}
          onPageChange={(p) => handlePageChange(null, p + 1)}
          onPageSizeChange={(n) =>
            handlePageLimitChange({ target: { value: n } })
          }
        />
      </Box>
    </Box>
  );
};

export default OptimizationRunDetail;
