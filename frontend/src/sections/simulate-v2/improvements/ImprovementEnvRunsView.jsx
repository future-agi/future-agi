import { useNavigate, useParams } from "react-router-dom";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore } from "../store";
import { protoRunId } from "../_mock/executionAdapter";
import RunsSummary from "../run/RunsSummary";

/**
 * Improvements — level 2 · env-scoped runs lens.
 *
 * Wraps the same RunsSummary the env's own Runs tab uses. State is
 * SHARED, not snapshotted: runs added, evals edited, winners chosen
 * here mutate the same env store the workspace reads, and any run
 * kicked off from the env workspace shows up here immediately.
 *
 * We only expose the Runs surface — no scenarios/contract/agent tabs.
 * Editing those is a bigger act and has its own home. The Open env
 * button in the header is the one-hop route into that home.
 */
export default function ImprovementEnvRunsView() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const { state } = useSimStore();
  const env = (state.myEnvironments || []).find((e) => e.id === envId);
  const envState = state.byEnv?.[envId];

  if (!env || !envState) {
    return (
      <Box sx={{ p: 4, textAlign: "center" }}>
        <Iconify icon="solar:question-circle-linear" width={32} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s1", color: "text.secondary", mt: 1 }}>
          Environment not found.
        </Typography>
        <Button
          size="small"
          onClick={() => navigate(paths.dashboard.simulate.improvements)}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          sx={{ mt: 2, typography: "s2" }}
        >
          Back to Improvements
        </Button>
      </Box>
    );
  }

  /* onStart mints a fresh runId and opens the run detail — same
     behaviour as the env's own Runs tab. The new run is recorded in
     the env store from inside the run detail, so it appears in the
     RunsSummary table as soon as we return here. */
  const startRun = () => {
    const runId = protoRunId(env.id, Date.now().toString(36));
    navigate(paths.dashboard.simulate.simulationRun(env.id, runId));
  };

  /* onGo routes to the env's workspace at the requested step. Clicking
     Edit evals from here takes you to the env's Evals tab, where the
     drawer lives; changes propagate back through the shared store. */
  const goToStep = (step) => {
    navigate(paths.dashboard.simulate.environmentStep(env.id, step));
  };

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* header */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{
          px: 3, py: 1.5, flexShrink: 0,
          borderBottom: "1px solid", borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Button
          size="small" variant="text"
          onClick={() => navigate(paths.dashboard.simulate.improvements)}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          sx={{ typography: "s2", color: "text.subtitle", minWidth: 0, px: 0.75 }}
        >
          Back
        </Button>
        <Iconify icon="solar:alt-arrow-right-linear" width={12} sx={{ color: "text.disabled" }} />
        <Typography noWrap sx={{ typography: "s1", fontWeight: 700, flex: 1, minWidth: 0 }}>
          {env.name}
        </Typography>
        <Button
          variant="outlined" size="small"
          onClick={() => navigate(paths.dashboard.simulate.environmentDetail(env.id))}
          startIcon={<Iconify icon="solar:settings-linear" width={14} />}
          sx={{
            typography: "s2", fontWeight: 600,
            color: "text.primary", borderColor: "divider",
            flexShrink: 0,
            "&:hover": { borderColor: "text.disabled" },
          }}
        >
          Configure environment
        </Button>
      </Stack>

      {/* body: the shared RunsSummary */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <RunsSummary env={env} envState={envState} onGo={goToStep} onStart={startRun} />
      </Box>
    </Box>
  );
}
