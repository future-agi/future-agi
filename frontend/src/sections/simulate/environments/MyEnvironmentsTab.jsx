import { useState } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  useMyEnvironments,
  useDeleteEnvironment,
} from "src/api/simulate-environments/environments";
import { ENTRY_TAB } from "./environmentOptions";
import { EMPTY_MESSAGE, DEFAULT_PAGE_SIZE } from "./myEnvironments.constants";
import useEnvironmentsTab from "./hooks/useEnvironmentsTab";
import MyEnvironmentsTable from "./MyEnvironmentsTable";
import EmptyState from "src/components/empty-state";

/**
 * Datasets-page pattern — a flex column that fills the remaining viewport
 * height so DataTablePagination inside MyEnvironmentsTable pins to the bottom
 * of the screen rather than floating right under the last row.
 */
export default function MyEnvironmentsTab() {
  const navigate = useNavigate();
  const { setTab } = useEnvironmentsTab();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const { data, isLoading, isPending, isError } = useMyEnvironments({
    page,
    pageSize,
  });
  const rows = data?.rows || [];
  const total = data?.total || 0;
  const loading = isLoading || isPending;
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
      ) : !loading && total === 0 ? (
        <EmptyState
          icon="solar:globe-linear"
          title={EMPTY_MESSAGE}
          description="Bring your agent in to create your first environment."
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => setTab(ENTRY_TAB.BUILD)}
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
          isLoading={loading}
          onOpen={onOpen}
          onRun={onRun}
          onDelete={onDelete}
        />
      )}
    </Box>
  );
}
