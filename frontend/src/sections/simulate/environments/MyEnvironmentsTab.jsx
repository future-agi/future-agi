import { Alert, Box, Stack, Typography, Button } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { paths } from "src/routes/paths";
import {
  useMyEnvironments,
  useDeleteEnvironment,
  useRunSimulation,
} from "src/api/simulate-environments/environments";
import { RUN_SIMULATION_COPY, ENTRY_TAB } from "./environmentOptions";
import { EMPTY_MESSAGE } from "./myEnvironments.constants";
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
  const { data, isLoading, isPending, error } = useMyEnvironments();
  const rows = data || [];
  const loading = isLoading || isPending;

  const deleteEnvironment = useDeleteEnvironment();
  const runSimulation = useRunSimulation();

  const onOpen = (env) =>
    navigate(paths.dashboard.simulate.environments.detail(env.id));
  const onRun = (env) =>
    runSimulation.mutate(env.id, {
      onSuccess: () => enqueueSnackbar(RUN_SIMULATION_COPY),
    });
  const onDelete = (env) => deleteEnvironment.mutate(env.id);

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
      {!loading && rows.length === 0 ? (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {error ? (
            <Alert severity="error" variant="outlined">
              {errorMessage(error)}
            </Alert>
          ) : (
            <Stack spacing={1.75} alignItems="center">
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {EMPTY_MESSAGE}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Bring your agent in to create your first environment.
              </Typography>
              <Button
                variant="contained"
                size="small"
                onClick={() => setTab(ENTRY_TAB.BUILD)}
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
              >
                Build an environment
              </Button>
            </Stack>
          )}
        </Box>
      ) : (
        <MyEnvironmentsTable
          rows={rows}
          isLoading={loading}
          onOpen={onOpen}
          onRun={onRun}
          onDelete={onDelete}
        />
      )}
    </Box>
  );
}
