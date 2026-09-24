import { useState } from "react";
import { Box, Stack, Typography, Button } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getHarnessJob } from "src/api/harness/harness";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  useMyEnvironments,
  useDeleteEnvironment,
} from "src/api/simulate-environments/environments";
import {
  harnessJobToEnvironment,
} from "src/api/simulate-environments/environment";
import { runSimulationTarget } from "src/api/simulate-environments/runs";
import { ENTRY_TAB } from "./environmentOptions";
import { EMPTY_MESSAGE, DEFAULT_PAGE_SIZE } from "./myEnvironments.constants";
import useEnvironmentsTab from "./hooks/useEnvironmentsTab";
import MyEnvironmentsTable from "./MyEnvironmentsTable";

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
  const { data, isLoading, isPending, isError } = useMyEnvironments({ page, pageSize });
  const rows = data?.rows || [];
  const total = data?.total || 0;
  const loading = isLoading || isPending;
  // A failed list must read as an error, not as "no environments yet".
  const showError = isError && !loading && total === 0;

  const deleteEnvironment = useDeleteEnvironment();

  const onOpen = (env) =>
    navigate(paths.dashboard.simulate.environments.detail(env.id));
  // The list payload has no `platform`, so fetch the job detail (which carries
  // the run-test bridge ids) and route to the product's execution target. A
  // fetch failure falls back to the product's Run Simulation entry.
  const onRun = (env) =>
    getHarnessJob(env.id)
      .then((item) =>
        navigate(runSimulationTarget(harnessJobToEnvironment(item).env)),
      )
      .catch(() => navigate(paths.dashboard.simulate.test));
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
              onClick={() => setTab(ENTRY_TAB.BUILD)}
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
          isLoading={loading}
          onOpen={onOpen}
          onRun={onRun}
          onDelete={onDelete}
        />
      )}
    </Box>
  );
}
