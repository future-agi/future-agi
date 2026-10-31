import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  useMyEnvironments,
  useDeleteEnvironment,
} from "src/api/simulate-environments/environments";
import { EMPTY_MESSAGE, DEFAULT_PAGE_SIZE } from "./myEnvironments.constants";
import useEnvironmentFilters from "./hooks/useEnvironmentFilters";
import MyEnvironmentsTable from "./MyEnvironmentsTable";
import EnvironmentFilters from "./components/EnvironmentFilters";
import { hasActiveFilters } from "src/api/simulate-environments/environmentListParams";
import { useDelayedLoading } from "src/hooks/use-delayed-loading";

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
  const { data, isLoading, isPending, isError, isPlaceholderData } = useMyEnvironments({
    page,
    pageSize,
    filters,
  });
  const rows = data?.rows || [];
  const total = data?.total || 0;
  const loading = isLoading || isPending;
  const filtering = hasActiveFilters(filters);
  // Skeleton rows on the first load and over the previous filter's rows while
  // the new page loads, but only for a load that outlasts 200 ms, and then for
  // at least 400 ms, so a quick response never flashes them.
  const showLoader = useDelayedLoading(loading || Boolean(isPlaceholderData));
  // Nothing built yet: the first-run empty state, with no filter bar.
  const firstRun = !loading && !isError && total === 0 && !filtering;
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
        gap: 1.5,
        overflow: "hidden",
        px: 2,
        pt: 2,
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
      ) : !loading && total === 0 && filtering ? (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {/* Clear is in the filter bar above. */}
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {NO_MATCH_MESSAGE}
          </Typography>
        </Box>
      ) : firstRun ? (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Stack spacing={1.75} alignItems="center">
            {/* The two lines read as one block, so keep them tight; the 1.75
                gap to the button below stays as the outer Stack spacing. */}
            <Stack spacing={0.5} alignItems="center">
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {EMPTY_MESSAGE}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Bring your agent in to create your first environment.
              </Typography>
            </Stack>
            <Button
              variant="contained"
              size="small"
              onClick={onBuild}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
            >
              Build an environment
            </Button>
          </Stack>
        </Box>
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
          isLoading={showLoader}
          pending={loading}
          onOpen={onOpen}
          onRun={onRun}
          onDelete={onDelete}
        />
      )}
    </Box>
  );
}

MyEnvironmentsTab.propTypes = { onBuild: PropTypes.func };
