import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Grid } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { resolveEval } from "src/api/simulate-environments/_fixtures/evalCatalog";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import PreflightItem from "./PreflightItem";
import EstimateRow from "./EstimateRow";
import RunsSummary from "./summary/RunsSummary";
import { RUNS_COPY, estimatedMinutes, estimatedCost } from "./runs.constants";

// The Runs tab. Before the first run it is a pre-flight card (what is about to
// happen, and whether the environment is ready to run at all). Once at least one
// run exists it becomes the run summary — the eval-score trend graph over a
// comparison table — matching the designer, where pre-flight moves into "Add
// more runs". `runs` is the real executions list for a completed harness job,
// empty for a client-only environment.
export default function RunsPanel({ env, envState, runs, onStart, onOpenRun, onGo }) {
  // A populated environment lands on the summary rather than the launcher.
  if (runs.length > 0) {
    return (
      <RunsSummary
        env={env}
        envState={envState}
        onOpenRun={onOpenRun}
        onGo={onGo}
      />
    );
  }

  const surface = getSurface(env.surface);
  const agent = envState.agent;
  const scenarioCount = envState.scenarios.length;
  const criticalCount = envState.scenarios.filter((s) => s.critical).length;
  // On a backed environment the caller hands us the overlaid state, so
  // `evals` here is §5's `evaluations.selected[]` — the count below and the
  // names in `evalSub` are the server's list, not the client store's. A
  // template/forked env has no backend detail and keeps the store's own
  // list; either way this reads one list, so the two lines cannot disagree.
  const evalCount = envState.evals.length;
  const ready = !!agent && scenarioCount > 0;

  const evalSub = evalCount
    ? envState.evals
        .slice(0, 2)
        .map((e) => resolveEval(e)?.name)
        .filter(Boolean)
        .join(", ")
    : RUNS_COPY.optional;

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
          {RUNS_COPY.title}
        </Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {RUNS_COPY.subtitle(env.name)}
        </Typography>
      </Box>

      <SectionCard
        title={RUNS_COPY.preflight}
        action={
          <Button
            variant="contained"
            color="primary"
            disabled={!ready}
            onClick={onStart}
            startIcon={<Iconify icon="solar:play-bold" width={16} />}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {RUNS_COPY.start}
          </Button>
        }
      >
        <Grid container spacing={0} sx={{ p: 2.5 }} rowSpacing={2}>
          <PreflightItem
            xs={6}
            md={3}
            label={RUNS_COPY.labels.environment}
            value={env.name}
            sub={surface.label}
            icon={surface.icon}
            color={surface.color}
            ok
          />
          <PreflightItem
            xs={6}
            md={3}
            label={RUNS_COPY.labels.agent}
            value={agent ? agent.name || RUNS_COPY.agentConnected : RUNS_COPY.agentNotConnected}
            sub={agent ? RUNS_COPY.connectionVerified : RUNS_COPY.required}
            icon="solar:cpu-bolt-linear"
            color={BUILD_TONES.accent}
            ok={!!agent}
            onFix={() => onGo("overview")}
          />
          <PreflightItem
            xs={6}
            md={3}
            label={RUNS_COPY.labels.scenarios}
            value={RUNS_COPY.tasks(scenarioCount)}
            sub={scenarioCount ? RUNS_COPY.critical(criticalCount) : RUNS_COPY.required}
            icon="solar:layers-minimalistic-linear"
            color={BUILD_TONES.accent}
            ok={scenarioCount > 0}
            onFix={() => onGo("scenarios")}
          />
          <PreflightItem
            xs={6}
            md={3}
            label={RUNS_COPY.labels.evals}
            value={RUNS_COPY.applied(evalCount)}
            sub={evalSub}
            icon="solar:shield-check-linear"
            color={BUILD_TONES.green}
            ok
            warn={evalCount === 0}
            onFix={() => onGo("evals")}
          />
        </Grid>

        <Stack
          direction="row"
          spacing={2}
          sx={{
            px: 2.5,
            py: 2,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          <EstimateRow
            icon="solar:clock-circle-linear"
            label={RUNS_COPY.estimate.duration}
            value={`~${estimatedMinutes(scenarioCount)} min`}
          />
          <EstimateRow
            icon="solar:bolt-circle-linear"
            label={RUNS_COPY.estimate.concurrency}
            value={RUNS_COPY.estimate.parallel}
          />
          <EstimateRow
            icon="solar:dollar-minimalistic-linear"
            label={RUNS_COPY.estimate.cost}
            value={`$${estimatedCost(scenarioCount)}`}
          />
        </Stack>
      </SectionCard>

      {/* Runs > 0 lands on RunsSummary above, so this history card is only ever
          the empty state — the run summary replaces it the moment a run lands. */}
      <Box sx={{ mt: 3 }}>
        <SectionCard title={RUNS_COPY.history(0)}>
          <EmptyState
            icon="solar:play-circle-linear"
            title={RUNS_COPY.empty.title}
            body={RUNS_COPY.empty.body}
          />
        </SectionCard>
      </Box>
    </Box>
  );
}

RunsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    surface: PropTypes.string,
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.shape({ typeId: PropTypes.string, name: PropTypes.string }),
    scenarios: PropTypes.arrayOf(PropTypes.shape({ critical: PropTypes.bool })),
    evals: PropTypes.arrayOf(
      PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
      ]),
    ),
  }).isRequired,
  runs: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string, status: PropTypes.string }))
    .isRequired,
  onStart: PropTypes.func,
  onOpenRun: PropTypes.func,
  onGo: PropTypes.func,
};
