import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Stack,
  Typography,
  Button,
  CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { RUNS_PAGE_SIZE } from "src/api/simulate-environments/runs";
import { ALL_RUNS_CAP } from "src/api/simulate-environments/useAllRuns";
import { paths } from "src/routes/paths";
import SectionCard from "../../../components/SectionCard";
import SoonChip from "../../../components/SoonChip";
import { useEnvironmentsStore } from "../../../store/useEnvironmentsStore";
import AddEvaluationDrawer from "../../evals/AddEvaluationDrawer";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { useRunsSummary } from "./useRunsSummary";
import { useRunsOverview } from "./useRunsOverview";
import RunsHeadline from "./RunsHeadline";
import EvalCheckChips from "./EvalCheckChips";
import PassRateTrend from "./PassRateTrend";
import SummaryTable from "./SummaryTable";
import AgentVersionScope from "./AgentVersionScope";
import RunsSelectionBar from "./RunsSelectionBar";
import WinnerDrawer from "./WinnerDrawer";
import { countCoveredScenarios, deriveEvals } from "./summaryData";
import RunCompare from "./RunCompare";

// The in-place two-run compare from the versioning work, hidden until its
// place in the product is decided; the compare page serves ticked runs.
const RUN_COMPARE_ENABLED = false;

// The Runs tab: the agent-version scope, the latest pass rate and the pass
// rate across every run, with the evals as chips that draw themselves on the
// line, over the runs table. Ticked runs compare on the compare page; one
// can be the baseline every other row is read against (kept per environment
// in the workspace store, since the API has no baseline yet). Choose winner
// ranks the runs; saving it, like deleting a run, has no API yet.
// The scope, the overview card and the runs bar (~420px) plus ~320px of rows.
const MIN_SUMMARY_PX = 800;

// The compare page's URL for these run ids: the earlier run first, so the
// change reads from it, and the baseline first when it is one of them.
export function compareUrl(envId, selected, baselineId) {
  const ids = [...selected]
    .sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0))
    .map((r) => r.id);
  const ordered = ids.includes(baselineId)
    ? [baselineId, ...ids.filter((id) => id !== baselineId)]
    : ids;
  return `${paths.dashboard.simulate.environments.compare(envId)}?runs=${ordered.join(",")}`;
}

export default function RunsSummary({
  env,
  envState,
  scenarioCount: scenarioCountProp,
  onOpenRun,
}) {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(RUNS_PAGE_SIZE);
  const [focusId, setFocusId] = useState(null);
  // The agent version in scope; null for all.
  const [scope, setScope] = useState(null);
  // Ticked summary rows, kept whole so a tick survives a page change.
  const [selected, setSelected] = useState([]);
  const [pickingWinner, setPickingWinner] = useState(false);
  const [comparing, setComparing] = useState(false);
  // TODO: compare is hidden until its place in the product is decided.
  const canCompare = RUN_COMPARE_ENABLED && !!env?.versioning?.enabled;
  const [addingEvals, setAddingEvals] = useState(false);
  const baselineId = useEnvironmentsStore(
    (s) => s.byEnv[env.id]?.baselineRunId ?? null,
  );
  const patchEnvState = useEnvironmentsStore((s) => s.patchEnvState);

  const { rows, evals, count, coveredScenarioCount, isLoading } =
    useRunsSummary(env, envState, { page, pageSize }, { scope });
  const overview = useRunsOverview(env, envState, focusId, rows, {
    scope,
    scoreAll: pickingWinner,
    baselineId,
  });
  const focusEval = overview.evals.find((e) => e.id === focusId) || null;
  const winnerEvals = useMemo(
    () => deriveEvals([...overview.rows, ...rows]),
    [overview.rows, rows],
  );
  // The server counts over every run of the environment; the page-local
  // fallback only serves mock runs and environments with no run-test.
  const scenarioCount =
    coveredScenarioCount ??
    countCoveredScenarios(rows, scenarioCountProp ?? envState.scenarios?.length ?? 0);
  const baseline = overview.baseline;
  // The headline and chips read the latest run against the chosen baseline,
  // or the run before it when there's none (or the baseline is the latest).
  const reference =
    baseline && baseline.id !== overview.latest?.id ? baseline : overview.previous;

  // A ticked run on this page reads its latest state: one no longer finished
  // (re-graded, say) can't be compared in place.
  const live = selected.map((s) => rows.find((r) => r.id === s.id) ?? s);
  const comparable =
    live.length === 2 && live.every((r) => r.runState === "finished");
  // The newer run is the one compared; the older is what it is compared with.
  const [current, against] = [...live].sort((a, b) => b.ordinal - a.ordinal);

  const toggle = (row) =>
    setSelected((prev) =>
      prev.some((r) => r.id === row.id)
        ? prev.filter((r) => r.id !== row.id)
        : [...prev, row],
    );
  // A tick on a run the new scope hides would act on a row nobody can see.
  const pickScope = (next) => {
    setScope(next);
    setPage(0);
    setSelected([]);
  };
  const setBaseline = (id) => {
    patchEnvState(env.id, { baselineRunId: id });
    setSelected([]);
  };

  // A deep link to ?tab=runs keeps the tab while the runs load; show that
  // they are loading rather than an empty summary.
  if (isLoading && rows.length === 0) {
    return (
      <Stack alignItems="center" sx={{ py: 6 }}>
        <CircularProgress size={20} />
      </Stack>
    );
  }

  return (
    // The tab's height: the overview keeps its size and the runs table takes
    // the rest, scrolling on its own, so the chart stays in view however many
    // runs there are. On a short panel it stops shrinking at room for the
    // chart plus a few table rows, and the panel scrolls instead.
    <Box
      sx={{
        p: 2,
        height: `max(100%, ${MIN_SUMMARY_PX}px)`,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <AgentVersionScope
        runs={overview.runs}
        value={scope}
        onChange={pickScope}
        shown={count}
      />
      <SectionCard sx={{ mb: 2, flexShrink: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          useFlexGap
          spacing={2}
          sx={{ px: 2.5, py: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          <RunsHeadline latest={overview.latest} previous={reference} />
          {overview.evals.length > 0 && (
            <EvalCheckChips
              evals={overview.evals}
              latest={overview.latest}
              previous={reference}
              focusId={focusId}
              onFocus={setFocusId}
            />
          )}
        </Stack>
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <PassRateTrend
            rows={overview.rows}
            focusEval={focusEval}
            capped={overview.capped}
            cap={ALL_RUNS_CAP}
            isLoading={overview.isLoading}
            onOpenRun={onOpenRun}
          />
        </Box>
      </SectionCard>

      <SectionCard
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{ px: 2.5, py: 1, flexShrink: 0, minHeight: 52 }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Stack direction="row" alignItems="baseline" spacing={1}>
              <Typography
                sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
              >
                Runs ({count})
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
              >
                across {scenarioCount}{" "}
                {scenarioCount === 1 ? "scenario" : "scenarios"}
              </Typography>
            </Stack>
            {baseline ? (
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
                >
                  Measured against{" "}
                  <Box
                    component="span"
                    sx={{
                      color: "text.primary",
                      fontWeight: "fontWeightSemiBold",
                    }}
                  >
                    {baseline.agentVersion
                      ? `${baseline.label} · agent ${baseline.agentVersion}`
                      : baseline.label}
                  </Box>
                  . Rates in points, everything else in percent.
                </Typography>
                <SoonChip
                  label="Noise" detail="How much a pass rate moves between identical runs, so a small change can be told apart from chance."
                  what="The noise band between two runs"
                  size="tiny"
                />
              </Stack>
            ) : (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle" }}
              >
                Select two or more to compare them scenario by scenario
              </Typography>
            )}
          </Box>
          {selected.length > 0 ? (
            <RunsSelectionBar
              selected={selected}
              baselineId={baselineId}
              onBaseline={setBaseline}
              onCompare={() =>
                canCompare && comparable
                  ? setComparing(true)
                  : navigate(compareUrl(env.id, selected, baselineId))
              }
              onClear={() => setSelected([])}
            />
          ) : (
            <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                onClick={() => setAddingEvals(true)}
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                sx={{ borderColor: "divider" }}
              >
                Add Evals
              </Button>
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                onClick={() => setPickingWinner(true)}
                startIcon={<Iconify icon="solar:cup-star-bold" width={16} />}
                sx={{ borderColor: "divider" }}
              >
                Choose winner
              </Button>
              {baseline && (
                <Button
                  size="small"
                  onClick={() => setBaseline(null)}
                  startIcon={<Iconify icon="solar:close-linear" width={14} />}
                  sx={{ color: "primary.main" }}
                >
                  Remove baseline
                </Button>
              )}
            </Stack>
          )}
        </Stack>

        <SummaryTable
          rows={rows}
          evals={evals}
          onOpenRun={onOpenRun}
          selectedIds={selected.map((r) => r.id)}
          onToggle={toggle}
          baseline={baseline}
        />
        {count > RUNS_PAGE_SIZE && (
          <Box
            sx={{
              flexShrink: 0,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <DataTablePagination
              page={page}
              pageSize={pageSize}
              total={count}
              onPageChange={setPage}
              onPageSizeChange={(n) => {
                setPageSize(n);
                setPage(0);
              }}
            />
          </Box>
        )}
      </SectionCard>

      <WinnerDrawer
        open={pickingWinner}
        onClose={() => setPickingWinner(false)}
        rows={overview.rows}
        evals={winnerEvals}
        onApply={() => {
          setPickingWinner(false);
          enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
        }}
      />
      {/* Opened in place; mounted only while open, since it reads the
          environment and the eval catalogue as it mounts. */}
      {addingEvals && (
        <AddEvaluationDrawer
          open
          env={env}
          onClose={() => setAddingEvals(false)}
        />
      )}
      {canCompare && comparable && (
        <RunCompare
          open={comparing}
          onClose={() => setComparing(false)}
          env={env}
          current={current}
          against={against}
        />
      )}
    </Box>
  );
}

RunsSummary.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    versioning: PropTypes.object,
  }).isRequired,
  envState: PropTypes.shape({ scenarios: PropTypes.array }).isRequired,
  scenarioCount: PropTypes.number,
  onOpenRun: PropTypes.func,
};
