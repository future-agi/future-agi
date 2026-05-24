import {
  Box,
  Button,
  MenuItem,
  Pagination,
  PaginationItem,
  Select,
  Stack,
  Typography,
  CircularProgress,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo, useRef, useState, useCallback } from "react";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import Iconify from "src/components/iconify";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import SvgColor from "src/components/svg-color";
import {
  getOptimizationRunListColumnDef,
  normalizeDatasetOptimizationRun,
} from "./common";
import { useDatasetOptimizationStoreShallow } from "./states";
import OptimizationNameRenderer from "./CellRenderers/OptimizationNameRenderer";
import StatusCellRenderer from "./CellRenderers/StatusCellRenderer";
import PropTypes from "prop-types";
import StopOptimizationModal from "./StopOptimizationModal";
import EmptyLayout from "../../../components/EmptyLayout/EmptyLayout";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const NoRowsOverlay = ({ title }) => (
  <Box
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      height: "100%",
      gap: 2,
    }}
  >
    <SvgColor
      src="/assets/icons/ic_empty_state.svg"
      sx={{ width: 64, height: 64, color: "text.secondary" }}
    />
    <Typography color="text.secondary">{title}</Typography>
  </Box>
);

NoRowsOverlay.propTypes = {
  title: PropTypes.string,
};

const DatasetOptimizationRunList = ({
  columnId,
  datasetId,
  onCreateClick,
  onSelectOptimization,
}) => {
  const { role } = useAuthContext();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const [page, setPage] = useState(1);
  const [pageLimit, setPageLimit] = useState(10);
  const [totalPages, setTotalPages] = useState(1);
  const queryClient = useQueryClient();
  const gridRef = useRef(null);
  const totalPagesRef = useRef(1);

  const { setOptimizationGridApi } = useDatasetOptimizationStoreShallow(
    (state) => ({
      setOptimizationGridApi: state.setOptimizationGridApi,
    }),
  );

  const checkQueryParams = useMemo(() => {
    const params = { page: 1, page_size: pageLimit };
    if (datasetId) params.dataset_id = datasetId;
    if (columnId) params.column_id = columnId;
    return params;
  }, [datasetId, columnId, pageLimit]);

  const { data: hasData, isLoading: isCheckingData } = useQuery({
    queryKey: ["dataset-optimization-runs", datasetId, columnId, pageLimit, 1],
    queryFn: () =>
      axios.get(endpoints.develop.datasetOptimization.list, {
        params: checkQueryParams,
      }),
    select: (response) => {
      const total =
        response?.data?.metadata?.total_rows ??
        response?.data?.result?.metadata?.total_rows ??
        0;
      return total > 0;
    },
    staleTime: 30 * 1000,
  });

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;

        const pageSize = pageLimit ?? request?.endRow - request?.startRow;
        const pageNumber = Math.floor(request?.startRow / pageSize) + 1;

        const queryParams = {
          page: pageNumber,
          page_size: pageSize,
        };

        if (datasetId) {
          queryParams.dataset_id = datasetId;
        }

        if (columnId) {
          queryParams.column_id = columnId;
        }

        const queryKey = [
          "dataset-optimization-runs",
          datasetId,
          columnId,
          pageSize,
          pageNumber,
        ];

        try {
          const { data } = await queryClient.fetchQuery({
            queryKey,
            queryFn: () =>
              axios.get(endpoints.develop.datasetOptimization.list, {
                params: queryParams,
              }),
          });

          const rows = (data?.table || data?.result?.table || []).map(
            normalizeDatasetOptimizationRun,
          );
          const total =
            data?.metadata?.total_rows ||
            data?.result?.metadata?.total_rows ||
            0;
          const calculatedTotalPages = Math.ceil(total / pageSize);

          if (calculatedTotalPages !== totalPagesRef.current) {
            totalPagesRef.current = calculatedTotalPages;
            setTotalPages(calculatedTotalPages);
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
        } catch (error) {
          logger.error("Failed to fetch optimization runs:", error);
          params.fail();
          params.api.showNoRowsOverlay();
        }
      },
      getRowId: (data) => data.id,
    }),
    [queryClient, pageLimit, datasetId, columnId],
  );

  const columnDefs = useMemo(() => {
    return getOptimizationRunListColumnDef();
  }, []);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: false,
      filter: false,
      resizable: true,
      suppressMenu: true,
      cellStyle: {
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      },
    }),
    [],
  );

  const components = useMemo(
    () => ({
      optimizationNameRenderer: OptimizationNameRenderer,
      statusCellRenderer: StatusCellRenderer,
    }),
    [],
  );

  const onPaginationChanged = useCallback(() => {
    if (gridRef.current?.api) {
      const currentPage = gridRef?.current?.api.paginationGetCurrentPage();
      setPage(currentPage + 1);
    }
  }, []);

  const handlePageChange = (event, value) => {
    if (gridRef?.current?.api) {
      gridRef?.current?.api?.paginationGoToPage(value - 1);
    }
  };

  const handlePageLimitChange = (e) => {
    const newLimit = Number(e.target.value);
    setPageLimit(newLimit);
    setPage(1);

    if (gridRef.current?.api) {
      gridRef?.current?.api?.paginationGoToPage(0);
      gridRef?.current?.api?.refreshServerSide({ purge: true });
    }
  };

  const handleRowClick = (params) => {
    const target = params.event?.target;

    if (target?.closest(".optimization-run-detail-refresh-button")) {
      return;
    }

    onSelectOptimization?.(params.data.id);
  };
  const handleSuccessOfStop = () => {
    gridRef?.current?.api.refreshServerSide({ purge: false });
  };

  if (isCheckingData) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flex: 1,
        }}
      >
        <CircularProgress size={42} />
      </Box>
    );
  }

  if (!hasData) {
    return (
      <EmptyLayout
        icon="/assets/icons/navbar/ic_dash_tasks.svg"
        title="Ready to make your prompts smarter"
        description="Run your first optimization to test, compare, and improve prompt performance."
        link="https://docs.futureagi.com/docs/optimization"
        linkText="Check docs"
        action={
          <Button
            variant="contained"
            color="primary"
            startIcon={
              <SvgColor
                sx={{
                  height: 16,
                  width: 16,
                  bgcolor: "primary.contrastText",
                }}
                src="/assets/icons/navbar/ic_optimize.svg"
              />
            }
            onClick={onCreateClick}
            disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
          >
            Run Optimization
          </Button>
        }
      />
    );
  }
  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        padding: 2,
        paddingTop: 0,
        minHeight: 0,
      }}
    >
      {/* Header with Create Button */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 2,
        }}
      >
        <Typography variant="h6">Optimization Runs</Typography>
        <Button
          size="small"
          color="primary"
          variant="contained"
          startIcon={
            <SvgColor
              src="/assets/icons/navbar/ic_optimize.svg"
              sx={{ width: 16, height: 16 }}
            />
          }
          onClick={onCreateClick}
          disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
          sx={{
            py: 0.5,
            px: 1.5,
          }}
        >
          Optimize Prompts
        </Button>
      </Box>

      {/* AG Grid */}
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
          }}
        >
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            onGridReady={(params) => {
              setOptimizationGridApi(params.api);
            }}
            rowHeight={65}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            components={components}
            suppressServerSideFullWidthLoadingRow={true}
            suppressRowClickSelection={true}
            pagination={true}
            paginationPageSize={pageLimit}
            paginationPageSizeSelector={false}
            rowModelType="serverSide"
            serverSideDatasource={dataSource}
            maxBlocksInCache={1}
            cacheBlockSize={pageLimit}
            serverSideInitialRowCount={pageLimit}
            rowStyle={{ cursor: "pointer" }}
            suppressContextMenu
            onPaginationChanged={onPaginationChanged}
            suppressPaginationPanel={true}
            getRowId={(params) => params.data.id}
            onCellClicked={handleRowClick}
            noRowsOverlayComponent={() => (
              <NoRowsOverlay title="No optimization runs found" />
            )}
          />
        </Box>

        {/* Pagination */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ p: 1, py: 1.5 }}
        >
          <Stack gap={1} direction="row" alignItems="center">
            <Typography
              typography="s2"
              color="text.primary"
              fontWeight="fontWeightRegular"
            >
              Results per page
            </Typography>

            <Select
              size="small"
              id="page-size-select"
              value={pageLimit}
              onChange={handlePageLimitChange}
              sx={{ height: 36, bgcolor: "background.paper" }}
            >
              {[10, 25, 50].map((size) => (
                <MenuItem key={size} value={size}>
                  {size}
                </MenuItem>
              ))}
            </Select>
          </Stack>

          <StopOptimizationModal onSuccess={handleSuccessOfStop} />
          <Pagination
            count={totalPages}
            variant="outlined"
            shape="rounded"
            page={page}
            color="primary"
            onChange={handlePageChange}
            renderItem={(item) => (
              <PaginationItem
                {...item}
                sx={{
                  borderRadius: "4px",
                  bgcolor: "background.paper",
                }}
                slots={{
                  previous: () => (
                    <Box display="flex" alignItems="center" gap={0.5}>
                      <Iconify
                        icon="octicon:chevron-left-24"
                        width={18}
                        height={18}
                      />
                      Back
                    </Box>
                  ),
                  next: () => (
                    <Box display="flex" alignItems="center" gap={0.5}>
                      Next
                      <Iconify
                        icon="octicon:chevron-right-24"
                        width={18}
                        height={18}
                      />
                    </Box>
                  ),
                }}
              />
            )}
          />
        </Stack>
      </Box>
    </Box>
  );
};

DatasetOptimizationRunList.propTypes = {
  datasetId: PropTypes.string,
  columnId: PropTypes.string,
  onCreateClick: PropTypes.func.isRequired,
  onSelectOptimization: PropTypes.func,
};

export default DatasetOptimizationRunList;
