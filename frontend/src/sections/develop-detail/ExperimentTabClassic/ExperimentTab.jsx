import { Box } from "@mui/material";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import { trackEvent, Events } from "src/utils/Mixpanel";
import ExperimentTabEmptyState from "../ExperimentTab/ExperimentTabEmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import {
  PAGE_SIZE,
  REFRESH_STATUSES,
  classicExperimentPath,
  useClassicExperimentColumns,
} from "./classicExperimentColumns";

// ── Component ────────────────────────────────────────────────────────

const ExperimentTab = React.forwardRef(
  ({ experimentSearch, setRowSelected, sdkRunShown }, ref) => {
    const { dataset } = useParams();
    const navigate = useNavigate();

    const [page, setPage] = useState(0);
    const [rowSelection, setRowSelection] = useState({});
    const refreshTimerRef = useRef(null);

    const debouncedSearch = useDebounce(experimentSearch, 400);

    // Reset page when search changes
    useEffect(() => {
      setPage(0);
    }, [debouncedSearch]);

    // ── Data fetching ────────────────────────────────────────────────
    const {
      data: response,
      error,
      isError,
      isLoading,
      isSuccess,
      refetch,
    } = useQuery({
      queryKey: ["experiment-list", dataset, page, debouncedSearch],
      queryFn: () =>
        axios.get(endpoints.develop.experiment.experimentListPaginated, {
          params: {
            dataset_id: dataset,
            page: page + 1,
            limit: PAGE_SIZE,
            search: debouncedSearch || undefined,
          },
        }),
      select: (res) => res?.data,
      keepPreviousData: true,
      refetchOnWindowFocus: false,
    });

    const rows = response?.results || [];
    const totalCount = response?.count || 0;

    // ── Auto-refresh when experiments are running ────────────────────
    const hasRunning = rows.some((r) => REFRESH_STATUSES.has(r.status));

    useEffect(() => {
      if (hasRunning) {
        refreshTimerRef.current = setInterval(() => refetch(), 10000);
      }
      return () => {
        if (refreshTimerRef.current) {
          clearInterval(refreshTimerRef.current);
          refreshTimerRef.current = null;
        }
      };
    }, [hasRunning, refetch]);

    // ── Expose refresh + clear to parent via ref ─────────────────────
    React.useImperativeHandle(
      ref,
      () => ({
        refreshExperimentTab: () => {
          refetch();
          setRowSelection({});
          setRowSelected([]);
        },
        clearRowSelection: () => {
          setRowSelection({});
          setRowSelected([]);
        },
      }),
      [refetch, setRowSelected],
    );

    // ── Selection sync ───────────────────────────────────────────────
    const handleRowSelectionChange = useCallback(
      (sel, selected) => {
        setRowSelection(sel);
        setRowSelected(selected);
      },
      [setRowSelected],
    );

    // ── Row click → navigate ─────────────────────────────────────────
    const handleRowClick = useCallback(
      (row) => {
        trackEvent(Events.expSelected, {
          datasetId: row.dataset,
          experimentName: row.name,
        });
        navigate(classicExperimentPath(row, dataset));
      },
      [dataset, navigate],
    );

    // ── Columns (TanStack format → DataTable) ────────────────────────
    const columns = useClassicExperimentColumns(refetch);

    // ── Read error ───────────────────────────────────────────────────
    // A failed read is not an empty list: with no experiments loaded to
    // show, say the read failed and offer a retry.
    if (isError && rows.length === 0) {
      return (
        <Box sx={{ flex: 1, p: 1.5 }}>
          <ErrorState
            thing="experiments"
            error={error}
            onRetry={() => refetch()}
          />
        </Box>
      );
    }

    // ── Empty state ──────────────────────────────────────────────────
    // Only after a read that succeeded and came back empty.
    if (isSuccess && rows.length === 0 && !debouncedSearch) {
      return <ExperimentTabEmptyState sdkRunShown={sdkRunShown} />;
    }

    return (
      <Box
        data-testid="classic-experiments-list"
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          p: "12px",
          overflow: "hidden",
        }}
      >
        <DataTable
          columns={columns}
          data={rows}
          isLoading={isLoading}
          rowCount={totalCount}
          enableSelection
          rowSelection={rowSelection}
          selectionScope={[
            dataset,
            page,
            PAGE_SIZE,
            experimentSearch,
            debouncedSearch,
          ]}
          onRowSelectionChange={handleRowSelectionChange}
          onRowClick={handleRowClick}
          getRowId={(row) => row.id}
          emptyMessage={
            debouncedSearch
              ? "No experiments match your search"
              : "No experiments yet"
          }
        />

        <DataTablePagination
          page={page}
          pageSize={PAGE_SIZE}
          total={totalCount}
          onPageChange={setPage}
          pageSizeOptions={[PAGE_SIZE]}
        />
      </Box>
    );
  },
);

ExperimentTab.displayName = "ExperimentTab";

ExperimentTab.propTypes = {
  experimentSearch: PropTypes.string,
  setRowSelected: PropTypes.func.isRequired,
  setCurrentTab: PropTypes.func,
  // an SDK run's panel is shown above the table (the page's run= parameter)
  sdkRunShown: PropTypes.bool,
};

export default ExperimentTab;
