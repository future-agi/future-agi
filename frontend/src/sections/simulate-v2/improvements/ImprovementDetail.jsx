import { useParams, useNavigate } from "react-router-dom";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore, useEnvState } from "../store";
import { getEnvironment } from "../_mock/environments";
import { findImprovement } from "../_mock/improvements";
import OptimizationRunView from "../run/fixmyagent/OptimizationRunView";

/**
 * Detail view for one improvement run — reuses `OptimizationRunView`
 * verbatim so the trials, per-scenario grid, best-so-far chart, code diff,
 * and winner banner are the same ones the drawer opens.
 *
 * Simulation-source: we have real env + envState so every panel renders.
 * Dataset-source: no env in the store — we render the same view with
 * empty env/envState. Env-specific bits (agent repo link, "open run"
 * jumps) degrade gracefully rather than crash.
 */
export default function ImprovementDetail() {
  const { improvementId } = useParams();
  const navigate = useNavigate();
  const { state } = useSimStore();

  const record = findImprovement(state, improvementId);
  const envId = record?.envId;
  /* useEnvState always runs — safe with an undefined envId (returns empty
     envState + no-op patch). Keeps the hook order stable regardless of
     whether the record was found. */
  const { envState, patch } = useEnvState(envId);
  const env = envId ? (getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId)) : null;

  if (!record) {
    return (
      <Stack alignItems="center" justifyContent="center" spacing={1.5} sx={{ height: "100%", p: 4, textAlign: "center" }}>
        <Iconify icon="solar:danger-triangle-linear" width={28} sx={{ color: "text.disabled" }} />
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Improvement not found</Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          It may have been removed, or the URL is stale.
        </Typography>
        <Button
          size="small" variant="contained" color="primary"
          onClick={() => navigate(paths.dashboard.simulate.improvements)}
          startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={14} />}
        >
          Back to Improvements
        </Button>
      </Stack>
    );
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, bgcolor: "background.paper" }}>
      <OptimizationRunView
        record={record}
        env={env || undefined}
        envState={envState}
        patch={patch}
        tasks={envState?.runs?.find((r) => r.id === record.fromRunId)?.tasks || []}
        onOpenTask={() => {}}
        onBack={() => navigate(paths.dashboard.simulate.improvements)}
        onClose={() => navigate(paths.dashboard.simulate.improvements)}
        onDone={() => navigate(paths.dashboard.simulate.improvements)}
        onRerun={() => {}}
      />
    </Box>
  );
}
