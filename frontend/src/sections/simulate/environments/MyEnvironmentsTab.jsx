import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  useMyEnvironments,
  useDeleteEnvironment,
} from "src/api/simulate-environments/environments";
import { hasActiveFilters } from "src/api/simulate-environments/environmentListParams";
import { EMPTY_MESSAGE, DEFAULT_PAGE_SIZE } from "./myEnvironments.constants";
import useEnvironmentFilters from "./hooks/useEnvironmentFilters";
import MyEnvironmentsTable from "./MyEnvironmentsTable";
import EnvironmentFilters from "./components/EnvironmentFilters";
import EmptyState from "src/components/empty-state";

const NO_MATCH_MESSAGE = "No environments match these filters";

/**
 * Datasets-page pattern — a flex column that fills the remaining viewport
 * height so DataTablePagination inside MyEnvironmentsTable pins to the bottom
 * of the screen rather than floating right under the last row.
 */
export default function MyEnvironmentsTab({ onBuild }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const { filters, setFilters } = useEnvironmentFilters();
  // A new filter starts from the first page, in the same render, so no request
  // asks for an old page the shorter result may not have.
  const changeFilters = (next) => {
    setPage(0);
    setFilters(next);
  };
  const { data, isLoading, isPending, isError, isPlaceholderData } =
    useMyEnvironments({ page, pageSize, filters });
  const filtersKey = JSON.stringify(filters);
  const settledFiltersKey = useRef(filtersKey);
  useEffect(() => {
    if (data && !isPlaceholderData) settledFiltersKey.current = filtersKey;
  }, [data, isPlaceholderData, filtersKey]);
  const filterLoading =
    Boolean(isPlaceholderData) && settledFiltersKey.current !== filtersKey;
  const rows = filterLoading ? [] : data?.rows || [];
  const total = filterLoading ? 0 : data?.total || 0;
  const loading = isLoading || isPending;
  const settled = !loading && !filterLoading;
  const filtering = hasActiveFilters(filters);
  // Nothing built yet: the first-run empty state, with no filter bar.
  const firstRun = settled && !isError && total === 0 && !filtering;
  // A failed list must read as an error, not as "no environments yet".
  const showError = isError && !loading && total === 0;

  const deleteEnvironment = useDeleteEnvironment();

  const onOpen = (env) =>
    navigate(paths.dashboard.simulate.environments.detail(env.id));
  const onRun = (env) =>
    navigate(
      `${paths.dashboard.simulate.environments.detail(env.id)}?tab=scenarios`,
    );
  const onDelete = (env) =>
    deleteEnvironment.mutate(env.id, {
      onError: (error) =>
        enqueueSnackbar(errorMessage(error), { variant: "error" }),
    });

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        px: 3,
        pb: 2,
      }}
    >
      {/* Not while the first page loads with no filter: an account with no
          environments would see the bar flash, then the first-run state. */}
      {!firstRun && !(loading && !filtering) && (
        <EnvironmentFilters
          value={filters}
          onChange={changeFilters}
          statusCounts={data?.statusCounts}
          shown={settled ? total : null}
        />
      )}
      {showError ? (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Stack spacing={0.5} alignItems="center">
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Couldn&apos;t load your environments.
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Something went wrong. Try again.
            </Typography>
          </Stack>
        </Box>
      ) : settled && total === 0 && filtering ? (
        // Clear is in the filter bar above.
        <EmptyState icon="solar:filter-linear" title={NO_MATCH_MESSAGE} />
      ) : firstRun ? (
        <EmptyState
          icon="solar:globe-linear"
          title={EMPTY_MESSAGE}
          description="Bring your agent in to create your first environment."
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={onBuild}
            >
              Build an environment
            </Button>
          }
        />
      ) : (
        <MyEnvironmentsTable
          rows={rows}
          total={total}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(n) => {
            setPageSize(n);
            setPage(0);
          }}
          // Dev's table dims the rows already shown while a new filter loads.
          isLoading={loading || Boolean(isPlaceholderData)}
          pending={!settled}
          onOpen={onOpen}
          onRun={onRun}
          onDelete={onDelete}
        />
      )}
    </Box>
  );
}

MyEnvironmentsTab.propTypes = { onBuild: PropTypes.func };
