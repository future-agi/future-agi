import { Box } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  useMyEnvironments,
  useDeleteEnvironment,
  useRunSimulation,
} from "src/api/simulate-environments/environments";
import { RUN_SIMULATION_COPY } from "./environmentOptions";
import MyEnvironmentsTable from "./MyEnvironmentsTable";

/**
 * Datasets-page pattern — a flex column that fills the remaining viewport
 * height so DataTablePagination inside MyEnvironmentsTable pins to the bottom
 * of the screen rather than floating right under the last row.
 */
export default function MyEnvironmentsTab() {
  const navigate = useNavigate();
  const { data, isLoading } = useMyEnvironments();
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
      <MyEnvironmentsTable
        rows={data || []}
        isLoading={isLoading}
        onOpen={onOpen}
        onRun={onRun}
        onDelete={onDelete}
      />
    </Box>
  );
}
