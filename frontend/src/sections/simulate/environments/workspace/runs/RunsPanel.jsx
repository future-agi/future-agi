import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Grid } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "src/api/simulate-environments/_fixtures/surfaces";
import { resolveEval } from "src/api/simulate-environments/_fixtures/evalCatalog";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import PreflightItem from "./PreflightItem";
import RunHistoryRow from "./RunHistoryRow";
import { RUNS_COPY } from "./runs.constants";

// Pre-flight + run history.
//
// Before a run costs anyone real money this shows exactly what is about to
// happen: how many tasks, against which agent, graded by what. Data is injected
// (no hook here) — `runs` is the real executions list for a completed harness
// job, empty for a client-only environment.
//
// NOTE: the designer's RunsPanel also carried a populated history block, but it
// is dead code (the component returns RunsSummary the moment a run exists), so
// it is not ported. The history below is a deliberately minimal, real-data list
// (see RunHistoryRow), not a RunsSummary port.
export default function RunsPanel({
  env,
  envState,
  runs,
  total,
  isLoading = false,
  isError = false,
  hasMore = false,
  isFetchingMore = false,
  fetchMore,
  onStart,
  onOpenRun,
  onGo,
}) {
  const surface = getSurface(env.surface);
  const agent = envState.agent;
  const scenarioCount = envState.scenarios.length;
  const criticalCount = envState.scenarios.filter((s) => s.critical).length;
  const evalCount = envState.evals.length;
  const ready = !!agent && scenarioCount > 0;

  // Loading and failure are not "no runs yet" — an environment whose history is
  // still in flight, or whose executions request failed, used to read as one
  // that had never been run.
  const renderHistory = () => {
    if (isLoading) {
      return (
        <Stack alignItems="center" sx={{ py: 4 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {RUNS_COPY.loading}
          </Typography>
        </Stack>
      );
    }
    if (isError) {
      return (
        <EmptyState
          icon="solar:danger-triangle-linear"
          title={RUNS_COPY.error.title}
          body={RUNS_COPY.error.body}
        />
      );
    }
    if (runs.length === 0) {
      return (
        <EmptyState
          icon="solar:play-circle-linear"
          title={RUNS_COPY.empty.title}
          body={RUNS_COPY.empty.body}
        />
      );
    }
    return (
      <>
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {runs.map((run) => (
            <RunHistoryRow key={run.id} run={run} onOpenRun={onOpenRun} />
          ))}
        </Stack>
        {hasMore && (
          <Stack
            alignItems="center"
            sx={{ py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
          >
            <Button size="small" disabled={isFetchingMore} onClick={() => fetchMore?.()}>
              {isFetchingMore ? RUNS_COPY.loadingMore : RUNS_COPY.loadMore}
            </Button>
          </Stack>
        )}
      </>
    );
  };

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
            sub={agent ? RUNS_COPY.agentEndpoint : RUNS_COPY.required}
            icon="solar:cpu-bolt-linear"
            color={BUILD_TONES.accent}
            ok={!!agent}
            onFix={() => onGo("summary")}
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
      </SectionCard>

      <Box sx={{ mt: 3 }}>
        <SectionCard title={RUNS_COPY.history(total ?? runs.length)}>
          {renderHistory()}
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
  total: PropTypes.number,
  isLoading: PropTypes.bool,
  isError: PropTypes.bool,
  hasMore: PropTypes.bool,
  isFetchingMore: PropTypes.bool,
  fetchMore: PropTypes.func,
  onStart: PropTypes.func,
  onOpenRun: PropTypes.func,
  onGo: PropTypes.func,
};
