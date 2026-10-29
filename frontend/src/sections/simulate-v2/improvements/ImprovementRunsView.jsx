import PropTypes from "prop-types";
import { requiredEvalIds } from "../_mock/evals";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import { mockCluster } from "src/api/errorFeed/mockAdapter";
import { alpha } from "@mui/material/styles";
import { neutralCheckboxSx } from "../components/primitives";
import { textTone } from "../components/textTone";
import { clickable, focusRing } from "../components/clickable";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents } from "../store";
import { improverSessionFor, useIntegrations } from "../_mock/integrations";
import ImproverAttempts from "../run/fixmyagent/ImproverAttempts";
import { evalSeries, runSummaries, trialSummaries } from "../_mock/comparison";
import { allMetrics, deltaAgainst } from "../_mock/winner";
import { Head, MetricCell, ScoreCell } from "../run/RunsTableCells";
import { compactTokens, creditsText } from "../run/runsTableFormat";
import {
  candidatesOf,
  chosenVerified,
  shippedCandidate,
} from "../run/fixmyagent/tradeoffs";
import WinnerChanges from "../run/fixmyagent/WinnerChanges";
import ScenarioOutcomes from "../run/fixmyagent/ScenarioOutcomes";
import { membersChanged, proposalMembers } from "../_mock/improvementPolicy";
import {
  improvementOutcome,
  IMPROVEMENT_PHASES,
  runsCountOf,
  searchSpend,
  improvementSpendParts,
  improvementTotalSpend,
  stopImprovement,
} from "../_mock/optimizationRuns";
import CostPartsTable from "./CostPartsTable";
import { downloadExperiment } from "../_mock/experimentExport";
import RaiseLimitBar from "./RaiseLimitBar";
import { creditsLabel } from "../_mock/metering";
import { RATE_PLAN } from "../_mock/rates";
import RunsOverview from "../run/RunsOverview";
import WinnerDrawer from "../run/WinnerDrawer";
import { improvementRowsOf } from "./improvementRows";
import { chartRunOf, trialRowsFor } from "./improvementRunsData";
import StatusPill from "./ImprovementStatusPill";
import {
  agentDefinitionOf,
  agentIdOf,
  envAgentKey,
} from "../_mock/agentRegistry";
import { behindProductionOf } from "./behindProduction";
import ImprovementStages from "./ImprovementStages";

const RED = "status.danger.fg";
const DELTA = 56;

/**
 * Improvements, level 2 — one improvement and the runs its search tried.
 * The pass rate across them, then the runs: pick two or more to compare them
 * scenario by scenario, or choose the winner by what matters here. A run
 * opens its own page (level 3).
 *
 * Choosing a winner only picks among runs that passed the checks; one the
 * search didn't verify reads as projected on the held-back set until it's
 * run on the full suite.
 *
 * While it's still running (`progress`), it's the same page: the stages it
 * is in where the verdict will be, and its runs arriving in the table as
 * they finish — each one a real run that opens on its own page.
 */
const STAGE_LABEL = {
  reproduce: IMPROVEMENT_PHASES.reproducing,
  search: IMPROVEMENT_PHASES.searching,
  verify: IMPROVEMENT_PHASES.verifying,
};

export default function ImprovementRunsView({
  env,
  envState,
  record,
  patch,
  onBack,
  backLabel,
  onOpenSearch,
  onVerify,
  onShip,
  onRecheck,
  progress = null,
  onOpenIssue,
  onOpenStartRun,
}) {
  const running = !!progress;
  /* The model the agent runs on — what a model lock holds every run to. */
  const agentModel = useMemo(() => {
    if (!envState?.agent) return null;
    const def = agentDefinitionOf({ agent: envState.agent, env, envState });
    return def.model?.visible ? def.model.name : null;
  }, [env, envState]);
  const navigate = useNavigate();
  const location = useLocation();
  const { agents, state: simState } = useConnectedAgents();
  const { prs, improverSessions } = useIntegrations();
  /* A coding agent working on this improvement alongside the search. */
  const session = useMemo(
    () => improverSessionFor(env.id, record.id, { improverSessions }),
    [env.id, record.id, improverSessions],
  );
  const [selected, setSelected] = useState([]);
  const [picking, setPicking] = useState(false);
  /* The experiment's ID — what agent-learn improve results / promote and
     the SDK take, so code reads this same experiment. */
  const experimentId = `${env.id}/${record.id}`;
  const [idCopied, setIdCopied] = useState(false);
  const copyId = () => {
    navigator.clipboard?.writeText(experimentId).catch(() => {});
    setIdCopied(true);
    setTimeout(() => setIdCopied(false), 1500);
  };
  /* Stop: asked first, with what has run and what it has cost so far. */
  const [stopAsk, setStopAsk] = useState(false);
  const stopNow = () => {
    setStopAsk(false);
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id ? stopImprovement(o, progress) : o,
      ),
    });
  };
  /* Flow 5's tabs once it has finished: the runs, what the kept change
     changes (each with its reason), and what it did to each scenario. */
  const [tab, setTab] = useState("runs");

  const row = useMemo(
    () =>
      improvementRowsOf({ state: simState, agents, prs }).find(
        (r) => r.envId === env.id && r.recordId === record.id,
      ) || null,
    [simState, agents, prs, env.id, record.id],
  );
  const candidates = useMemo(() => candidatesOf(record), [record]);
  const allRuns = useMemo(
    () => trialRowsFor(env, envState, record, candidates),
    [env, envState, record, candidates],
  );
  /* Running: only the runs that have finished so far. */
  const runsDone = running ? progress.runsDone : null;
  const runs = useMemo(
    () => (runsDone == null ? allRuns : allRuns.slice(0, runsDone)),
    [allRuns, runsDone],
  );
  /* The run still going, shown as a row so the table never looks stalled. */
  const pendingN =
    running &&
    progress.stage === "search" &&
    progress.runsDone < progress.runsTotal
      ? progress.runsDone + 1
      : null;
  /* While running, the best run so far that nothing rejected. */
  const bestSoFarN = running
    ? [...runs].reverse().find((r) => r.improved && !r.why)?.n || null
    : null;
  const chartRuns = useMemo(
    () => runs.map((r) => chartRunOf(r, record)),
    [runs, record],
  );
  const searchWinner = record.result?.winner?.n || null;
  const keptN = running ? null : record.preferredTrial || searchWinner;
  const kept = runs.find((r) => r.n === keptN) || null;
  /* A winner you chose wasn't the one the search verified — it runs on the
     full suite, held-back scenarios included, before it can be reviewed. */
  const needsVerify = !!kept && !chosenVerified(record);
  /* The verdict leads: what it found, the held-back figure, the constraints. */
  const outcome = useMemo(
    () => improvementOutcome(record, { model: agentModel }),
    [record, agentModel],
  );
  const shippable =
    ["ready", "stopped-early", "budget"].includes(outcome.ending.key) &&
    !!record.result?.winner;
  const result = record.result;
  /* What ships — the search's pick or the run someone chose — and which
     members of a multi-agent its changes touch. */
  const shipped = shippedCandidate(record) || result?.winner || null;
  const members = useMemo(() => {
    const ctx = {
      agent: envState?.agent || null,
      limits: record.limits,
      target: record.targetMember,
    };
    const proposals = shipped?.proposals || [];
    return membersChanged(proposals, ctx).hasMembers
      ? proposalMembers(proposals, ctx)
      : null;
  }, [shipped, envState, record.limits, record.targetMember]);
  /* A stopped one has no kept change and no held-back results: its trials
     and what it cost, nothing to choose. */
  const stopped = outcome?.ending?.key === "stopped";
  const tabbed = !running && !!result;
  const view = tabbed ? tab : "runs";
  /*
    Every pass rate here is on the target scenarios, so a run's change is
    read against what the agent scored on those same scenarios before
    (result.base) — never the full suite the starting run covered. A
    baseline set from this page's own runs is the same set too.
  */
  const targetDelta = (rate) => {
    if (rate == null || result?.base == null) return null;
    const d = Math.round(rate - result.base);
    return d === 0
      ? { text: "same", better: null, flat: true }
      : { text: `${Math.abs(d)} pts`, up: d > 0, better: d > 0, flat: false };
  };

  /* Each run's numbers, and the run the improvement started from — what
     every change in the table is read against. */
  const summaries = useMemo(
    () =>
      trialSummaries(env, envState).filter(
        (t) => t.selfImprovementId === record.id,
      ),
    [env, envState, record.id],
  );
  const envRuns = useMemo(() => runSummaries(env, envState), [env, envState]);
  const startRun = envRuns.find((r) => r.id === record.fromRunId) || null;
  /* Production may have moved on since this fix started: its proof is against
     the version it started from, so a fix still waiting to ship says so. */
  const behind = useMemo(
    () => behindProductionOf({ record, env, envState, agents }),
    [record, env, envState, agents],
  );
  /* Behind and not re-checked: Ship waits for the re-check. */
  const holdShip = !!behind && !behind.recheck;
  /* A repo agent's patch that conflicts with newer commits: a person reviews
     the rebase before it's re-checked — nothing stale is applied. */
  const rebaseFirst = !!behind?.conflict && !behind.conflict.resolved;
  const [rebaseOpen, setRebaseOpen] = useState(false);
  const acceptRebase = () => {
    setRebaseOpen(false);
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id
          ? {
              ...o,
              rebase: {
                onto: behind.prodLabel,
                path: behind.conflict.path,
                at: new Date().toISOString(),
              },
            }
          : o,
      ),
    });
  };
  const recheckRun = behind?.recheck
    ? (envState?.runs || []).find((r) => r.id === behind.recheck.runId) || null
    : null;
  /* Required ones are marked — only they weigh like a pass/fail signal. */
  const evals = useMemo(() => {
    const required = new Set(requiredEvalIds(envState?.evals || []));
    return evalSeries(envRuns, envState).map((e) => ({
      id: e.id,
      name: e.name,
      required: required.has(e.id),
    }));
  }, [envRuns, envState]);
  const metrics = useMemo(
    () => Object.fromEntries(allMetrics(evals).map((m) => [m.id, m])),
    [evals],
  );
  const tableRows = useMemo(
    () =>
      runs.map((r) => ({
        ...r,
        s: summaries.find((t) => t.trialN === r.n) || null,
      })),
    [runs, summaries],
  );
  /* What the table's changes are read against — the run it started from,
     unless one of its own runs was set as the baseline (the same choice the
     environment's Runs tab offers). Kept on the improvement. */
  const baselineRow = record.baselineRunId
    ? tableRows.find((r) => r.runId === record.baselineRunId && r.s) || null
    : null;
  const baseline = baselineRow?.s || startRun;
  const setBaseline = (runId) =>
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id
          ? {
              ...o,
              baselineRunId:
                runId && runId !== o.baselineRunId ? runId : undefined,
            }
          : o,
      ),
    });
  /* The chart leads with the winner, read against the run the improvement
     started from — the same change its row shows — not the latest run. */
  /* While it runs, the best run so far leads — never whichever finished last. */
  const lead =
    kept || (running ? runs.find((r) => r.n === bestSoFarN) || null : null);
  const headline = useMemo(() => {
    if (!lead) return null;
    const run = chartRuns.find((c) => c.id === lead.runId);
    if (!run) return null;
    const m = tableRows.find((r) => r.n === lead.n)?.s;
    return {
      run,
      /* Scored on the target scenarios, not the full suite its run page shows. */
      metric: "pass rate on the target scenarios",
      label: `${lead.name} · ${kept ? "winner" : "best so far"}`,
      delta: targetDelta(m?.passRate ?? lead.score),
      /* Not "Run N" — this page's own runs are numbered too. */
      against: "the agent before the change, on the same scenarios",
    };
  }, [lead, kept, chartRuns, tableRows]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Run · Status · Pass · Duration · Tokens · Cost · Said, not done · Return,
     then a column per grader — the Runs table's columns. */
  const template = `40px minmax(220px, 380px) 150px ${[...Array(6).fill(124), ...evals.map(() => 128)].map((c) => `minmax(${c}px, 1fr)`).join(" ")}`;
  const minWidth = 20 + 40 + 220 + 150 + 6 * 124 + evals.length * 128 + 8;

  /* Only runs that passed the checks are in the race. */
  const field = useMemo(() => {
    const eligible = new Set(
      candidates.filter((c) => c.eligible).map((c) => c.n),
    );
    return trialSummaries(env, envState)
      .filter(
        (t) => t.selfImprovementId === record.id && eligible.has(t.trialN),
      )
      .map((t) => ({ ...t, partial: false }));
  }, [env, envState, record.id, candidates]);

  const choose = ({ runId }) => {
    const n = Number(/-t(\d+)$/.exec(runId)?.[1]) || null;
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id
          ? { ...o, preferredTrial: n && n !== searchWinner ? n : undefined }
          : o,
      ),
    });
    setPicking(false);
  };

  const toggle = (id) =>
    setSelected((s) =>
      s.includes(id) ? s.filter((x) => x !== id) : [...s, id],
    );
  /* Compare comes back here, to this improvement's runs. */
  const compare = () =>
    navigate(
      `${paths.dashboard.simulate.simulationCompare(env.id)}?runs=${selected.join(",")}`,
      {
        state: {
          back: {
            to: `${location.pathname}${location.search}`,
            label: row?.name || "the improvement",
            state: location.state,
          },
        },
      },
    );
  const openRun = (r) =>
    navigate(paths.dashboard.simulate.simulationRun(env.id, r.runId));

  const statusOf = (r) => {
    if (running) {
      if (r.why) {
        return (
          <Tooltip arrow title={`Not eligible — ${r.why}`}>
            <Typography noWrap sx={{ typography: "s2", color: RED }}>
              Rejected
            </Typography>
          </Tooltip>
        );
      }
      return r.n === bestSoFarN ? (
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
          Best so far
        </Typography>
      ) : (
        <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
          No better
        </Typography>
      );
    }
    if (r.n === keptN) {
      return (
        <Tooltip
          arrow
          title={
            r.n === searchWinner
              ? "The search kept this trial — verified on the held-back scenarios."
              : needsVerify
                ? "Chosen by you — not yet run on the held-back scenarios. Verify it before review."
                : "Chosen by you and run on the full suite."
          }
        >
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            Winner
            <Box
              component="span"
              sx={{ fontWeight: 400, color: "text.subtitle" }}
            >
              {r.n === searchWinner
                ? ""
                : needsVerify
                  ? " · not verified"
                  : " · your pick"}
            </Box>
          </Typography>
        </Tooltip>
      );
    }
    if (r.why) {
      return (
        <Tooltip arrow title={`Not eligible — ${r.why}`}>
          <Typography noWrap sx={{ typography: "s2", color: RED }}>
            Rejected
          </Typography>
        </Tooltip>
      );
    }
    return (
      <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
        Passed, not picked
      </Typography>
    );
  };

  return (
    <Box
      sx={{
        height: "100%",
        overflow: "auto",
        p: 2,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box sx={{ mb: 2, flexShrink: 0 }}>
        {/* Two lines: back · name · status, then versions · where · the issue it fixes. */}
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Tooltip arrow title={`Back to ${backLabel}`}>
                <IconButton
                  size="small"
                  aria-label={`Back to ${backLabel}`}
                  onClick={onBack}
                  sx={{ color: "text.secondary", ml: -0.5 }}
                >
                  <Iconify icon="solar:arrow-left-linear" width={16} />
                </IconButton>
              </Tooltip>
              <Typography
                noWrap
                sx={{ fontSize: 16, lineHeight: "24px", fontWeight: 600 }}
              >
                {row?.name || record.name}
              </Typography>
              {running ? (
                <StatusPill
                  ending={{
                    key: "running",
                    label:
                      STAGE_LABEL[progress.stage] ||
                      IMPROVEMENT_PHASES.searching,
                  }}
                />
              ) : (
                <StatusPill ending={row?.status} headline={row?.headline} />
              )}
            </Stack>
            {/* The version pair, shown the same way on every screen (RSI PRD),
                then where it ran and what started it; the id on hover. */}
            <Tooltip arrow placement="bottom-start" title={record.id}>
              <Typography
                noWrap
                sx={{
                  typography: "s2",
                  color: "text.subtitle",
                  pl: 4.5,
                  width: "fit-content",
                  maxWidth: "100%",
                }}
              >
                {[
                  row?.identity,
                  env.name,
                  /* The issue it fixes, by name — not just its ID. */
                  record.trigger?.issueId
                    ? `${record.trigger.issueId} ${mockCluster(record.trigger.issueId)?.error?.name || ""}`.trim()
                    : row?.trigger?.label,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Tooltip>
          </Box>
          {/* Stop while it runs — what finished stays; nothing is chosen. */}
          {running && patch && (
            <>
              <Button
                size="small"
                variant="outlined"
                onClick={() => setStopAsk(true)}
                startIcon={
                  <Iconify icon="solar:stop-circle-linear" width={16} />
                }
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                Stop
              </Button>
              <Dialog
                open={stopAsk}
                onClose={() => setStopAsk(false)}
                maxWidth="xs"
                fullWidth
              >
                <DialogTitle sx={{ typography: "s1", fontWeight: 600 }}>
                  {`Stop ${row?.name || record.name}?`}
                </DialogTitle>
                <DialogContent>
                  <Typography sx={{ typography: "s2" }}>
                    {`${progress.runsDone} of up to ${progress.runsMax || progress.runsTotal} trials ${progress.runsDone === 1 ? "has" : "have"} run · ${creditsLabel(improvementTotalSpend(stopImprovement(record, progress), env.surface))} spent so far.`}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary", mt: 1 }}
                  >
                    None of them has been checked on the held-back scenarios, so
                    nothing can be chosen — today&apos;s version stays. The
                    trials that ran stay on this page; nothing more is spent.
                  </Typography>
                </DialogContent>
                <DialogActions sx={{ px: 3, pb: 2.5 }}>
                  <Button
                    size="small"
                    onClick={() => setStopAsk(false)}
                    sx={{ color: "text.secondary" }}
                  >
                    Keep running
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    onClick={stopNow}
                  >
                    Stop
                  </Button>
                </DialogActions>
              </Dialog>
            </>
          )}
          <Tooltip
            arrow
            title={
              idCopied
                ? "Copied"
                : `Copy experiment ID · ${experimentId} — for agent-learn improve results and the SDK`
            }
          >
            <IconButton
              size="small"
              aria-label="Copy experiment ID"
              onClick={copyId}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                flexShrink: 0,
              }}
            >
              <Iconify
                icon={
                  idCopied ? "solar:check-circle-linear" : "solar:copy-linear"
                }
                width={16}
              />
            </IconButton>
          </Tooltip>
          {/* The whole experiment as a file — baseline, scenarios, changes,
              trials, result and spend; no credentials. */}
          {!running && (
            <Tooltip arrow title="Export experiment">
              <IconButton
                size="small"
                aria-label="Export experiment"
                onClick={() => downloadExperiment(record, env)}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  flexShrink: 0,
                }}
              >
                <Iconify icon="solar:download-minimalistic-linear" width={16} />
              </IconButton>
            </Tooltip>
          )}
          {!running && (
            <Button
              size="small"
              variant="outlined"
              onClick={onOpenSearch}
              startIcon={
                <Iconify icon="solar:shield-check-linear" width={16} />
              }
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              How it was decided
            </Button>
          )}
          {kept && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => openRun(kept)}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              {`Review ${kept.name}`}
            </Button>
          )}
          {/* Only a verified change ships: a run chosen over the search's
              pick is verified first. */}
          {kept &&
            shippable &&
            (needsVerify ? (
              <Tooltip
                arrow
                title="Runs it on the full suite — only a verified change can ship."
              >
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={onVerify}
                  startIcon={<Iconify icon="solar:play-bold" width={14} />}
                  sx={{ flexShrink: 0 }}
                >
                  {`Verify ${kept.name}`}
                </Button>
              </Tooltip>
            ) : onShip && holdShip && rebaseFirst && patch ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => setRebaseOpen(true)}
                startIcon={
                  <Iconify icon="solar:git-pull-request-linear" width={14} />
                }
                sx={{ flexShrink: 0 }}
              >
                Review rebase
              </Button>
            ) : onShip && holdShip && onRecheck ? (
              /* Its proof is on an older version: re-checked first. */
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={onRecheck}
                startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
                sx={{ flexShrink: 0 }}
              >
                {`Re-check on ${behind.prod}`}
              </Button>
            ) : onShip ? (
              <Tooltip
                arrow
                title={
                  row?.output
                    ? ""
                    : `Makes ${kept.name}'s version the default in ${env?.name || "this environment"}. A rejected trial can't ship.`
                }
              >
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={onShip}
                  startIcon={
                    <Iconify
                      icon="solar:upload-minimalistic-linear"
                      width={14}
                    />
                  }
                  sx={{ flexShrink: 0 }}
                >
                  {row?.output ? "Hand-over" : "Ship"}
                </Button>
              </Tooltip>
            ) : null)}
        </Stack>
      </Box>

      {rebaseFirst && (
        <Dialog
          open={rebaseOpen}
          onClose={() => setRebaseOpen(false)}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle sx={{ typography: "s1", fontWeight: 600 }}>
            {`Rebase onto ${behind.prod}`}
          </DialogTitle>
          <DialogContent>
            <Typography sx={{ typography: "s2" }}>
              {`${behind.since} commit${behind.since === 1 ? "" : "s"} on main changed ${behind.conflict.path} after this patch was written, in the lines it edits.`}
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 1 }}
            >
              {`The rebase keeps main's changes and puts this fix's lines after them. Accept it to re-check the rebased patch on ${behind.prod} — the proof on ${behind.base} doesn't carry over.`}
            </Typography>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2.5 }}>
            <Button
              size="small"
              onClick={() => setRebaseOpen(false)}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={acceptRebase}
            >
              Accept rebase
            </Button>
          </DialogActions>
        </Dialog>
      )}

      {/* Fell behind production while it waited — re-check before shipping. */}
      {behind &&
        !running &&
        !["released", "watching", "resolved"].includes(row?.status?.key) && (
          <Stack
            direction="row"
            spacing={1.25}
            sx={{
              mb: 1.5,
              px: 2,
              py: 1.125,
              flexShrink: 0,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            <Iconify
              icon="solar:history-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >
                {behind.git
                  ? `main has moved on ${behind.since} commit${behind.since === 1 ? "" : "s"} since this fix started.`
                  : `${behind.prod} is in production now — ${behind.since} version${behind.since === 1 ? "" : "s"} after this fix started.`}
              </Box>
              {behind.recheck
                ? ` Re-checked on ${behind.prod}${recheckRun ? ` in Run ${recheckRun.ordinal}${recheckRun.finishedAt ? "" : " (still running)"}` : ""} — compare it with its proof on ${behind.base} before it ${behind.git ? "is merged" : "ships"}.`
                : rebaseFirst
                  ? ` Its patch conflicts with those commits in ${behind.conflict.path}. Review the rebase onto ${behind.prod} first — nothing is applied until you accept it — then it's re-checked there before it is merged.`
                  : behind.git
                    ? ` Its patch is rebased onto ${behind.prod}${behind.conflict ? ` (${behind.conflict.path} merged by you)` : ""} and re-checked there before it is merged — its proof is on ${behind.base}.`
                    : ` Its proof is on ${behind.base}, so it's re-checked on ${behind.prod} before it ships.`}
            </Typography>
          </Stack>
        )}

      {/* The verdict first: what it found, the held-back figure, the
          constraints. While it runs, the stage it's in takes that place. */}
      {running ? (
        <ImprovementStages
          record={record}
          envState={envState}
          progress={progress}
          model={agentModel}
          onOpenIssue={onOpenIssue}
          onOpenRun={onOpenStartRun}
        />
      ) : (
        <Verdict
          outcome={outcome}
          result={result}
          targetNow={kept?.score ?? null}
        />
      )}

      {/* No safe change: never a dead end — what to try instead. */}
      {!running && outcome?.ending?.key === "no-fix" && (
        <NoFixNext
          issueId={record.trigger?.issueId || null}
          onOpenIssue={onOpenIssue}
          onOpenRules={
            envState?.agent
              ? () =>
                  navigate(
                    `${paths.dashboard.agents}/connected/${agentIdOf(envAgentKey(envState.agent, env.id))}?tab=constraints`,
                  )
              : null
          }
        />
      )}

      {/* Stopped partway at its budget or time cap: raise it here and continue. */}
      {!running && record.stoppedAtBudget?.trials > 0 && (
        <RaiseLimitBar
          record={record}
          env={env}
          envState={envState}
          patch={patch}
        />
      )}

      {tabbed && (
        <Tabs
          value={view}
          onChange={(_, v) => setTab(v)}
          sx={{
            mb: 2,
            flexShrink: 0,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Tab value="runs" label={`Trials (${runs.length})`} />
          {!stopped && <Tab value="changes" label="Changes" />}
          {!stopped && <Tab value="scenarios" label="Per scenario" />}
          <Tab value="cost" label="Cost" />
        </Tabs>
      )}

      {view !== "runs" && (
        <Box
          sx={{
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          {view === "cost" ? (
            /* What this improvement spent, part by part, on the versions in
               its header; a stop at the budget keeps what it found. */
            <Box sx={{ p: 2 }}>
              <CreditLedger
                spent={improvementSpendParts(record, env?.surface).reduce(
                  (n, p) => n + (p.usd || 0),
                  0,
                )}
                budget={record.budget}
                running={running}
              />
              <CostPartsTable
                parts={improvementSpendParts(record, env?.surface)}
                totalLabel="Spent"
                note={[
                  record.budget
                    ? `Budget ${creditsLabel(record.budget)}`
                    : null,
                  record.stoppedAtBudget
                    ? "stopped at its budget — the best it found is kept"
                    : null,
                  `${RATE_PLAN.name} · ${RATE_PLAN.version} rates`,
                  "each part charged once; its trials' own Cost tabs list their calls",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              />
            </Box>
          ) : view === "changes" ? (
            <WinnerChanges
              env={env}
              winner={shipped}
              record={record}
              members={members}
            />
          ) : (
            <ScenarioOutcomes result={result} winner={result.winner || null} />
          )}
        </Box>
      )}

      {view === "runs" && chartRuns.length > 0 && (
        <Box sx={{ mb: 2, flexShrink: 0 }}>
          <RunsOverview
            runs={chartRuns}
            unit="trial"
            evals={[]}
            headline={headline}
            onOpenRun={(r) =>
              navigate(paths.dashboard.simulate.simulationRun(env.id, r.id))
            }
          />
        </Box>
      )}

      {view === "runs" && (
        <>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ mb: 1.25, flexShrink: 0 }}
          >
            {/* Finished, the Runs tab names them; running, the heading does. */}
            {/* The run count is a maximum: "of up to 8" while it searches, and
            an early stop said once the runs have played. */}
            {running &&
              (() => {
                const c = runsCountOf(progress, record.result);
                return (
                  <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                    {c.stopped
                      ? `Trials (${runs.length}) · ${c.stopped}`
                      : `Trials (${runs.length} of ${c.upTo ? "up to " : ""}${c.max})`}
                  </Typography>
                );
              })()}
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
            >
              {running && progress.stage === "reproduce" ? (
                "Each trial is the agent with one change, simulated on this environment — open one to see its conversations"
              ) : selected.length ? (
                `${selected.length} selected`
              ) : baselineRow ? (
                <>
                  Measured against{" "}
                  <Box
                    component="span"
                    sx={{ color: "text.primary", fontWeight: 600 }}
                  >
                    {baselineRow.name || `Trial ${baselineRow.n}`}
                  </Box>{" "}
                  — rates in points, everything else in percent
                </>
              ) : (
                `Select two or more to compare them scenario by scenario · pass rates are on the target scenarios, against ${result?.base ?? "—"}% before the change${startRun ? `; other columns against ${env.name} Run ${startRun.ordinal}` : ""}`
              )}
            </Typography>
            {selected.length > 0 && (
              <Button
                size="small"
                onClick={() => setSelected([])}
                sx={{ color: "text.secondary", minWidth: 0 }}
              >
                Clear
              </Button>
            )}
            {/* One run is a baseline; two or more is a comparison — as on the
            environment's Runs tab. */}
            {selected.length === 1 && patch ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => {
                  setBaseline(selected[0]);
                  setSelected([]);
                }}
                startIcon={
                  <Iconify icon="solar:transfer-vertical-linear" width={16} />
                }
              >
                {selected[0] === record.baselineRunId
                  ? "Clear baseline"
                  : "Set as baseline"}
              </Button>
            ) : selected.length === 0 && baselineRow && patch ? (
              <Button
                size="small"
                onClick={() => setBaseline(null)}
                startIcon={<Iconify icon="solar:close-linear" width={14} />}
                sx={{ color: "primary.main" }}
              >
                Remove baseline
              </Button>
            ) : null}
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={selected.length < 2}
              onClick={compare}
              startIcon={
                <Iconify icon="solar:transfer-horizontal-linear" width={16} />
              }
            >
              Compare
            </Button>
            {!stopped && (
              <Tooltip
                arrow
                title={
                  running
                    ? "Once every trial has finished and been checked."
                    : field.length < 2
                      ? "Fewer than two trials passed the checks — there's nothing to choose between."
                      : ""
                }
                disableHoverListener={!running && field.length >= 2}
              >
                <Box component="span">
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={running || field.length < 2}
                    onClick={() => setPicking(true)}
                    startIcon={
                      <Iconify icon="solar:cup-star-bold" width={16} />
                    }
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Choose winner
                  </Button>
                </Box>
              </Tooltip>
            )}
          </Stack>

          <Box
            sx={{
              flex: 1,
              minHeight: 240,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "auto",
              bgcolor: "background.paper",
            }}
          >
            <Box sx={{ minWidth }}>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: template,
                  alignItems: "flex-end",
                  pl: 2.5,
                  pt: 1.5,
                  pb: 1,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box />
                <Head>Trial</Head>
                <Head>Status</Head>
                <Head
                  right
                  title="Scenarios this trial passed, on the target scenarios it was scored on"
                >
                  Pass
                </Head>
                <Head right title="Average duration per task">
                  Duration
                </Head>
                <Head right>Tokens</Head>
                <Head right>Cost</Head>
                <Head
                  right
                  title="Tasks where the agent said it was done but the world shows it wasn't"
                >
                  Said, not done
                </Head>
                <Head
                  right
                  title="Average score the environment gave each conversation"
                >
                  Return
                </Head>
                {evals.map((e, i) => (
                  <Head
                    key={e.id}
                    right
                    divider={i === 0}
                    last={i === evals.length - 1}
                    title={e.name}
                    wrap
                  >
                    {e.name.replace(/_/g, "_\u200b")}
                  </Head>
                ))}
              </Box>
              <Stack
                divider={
                  <Box
                    sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                  />
                }
              >
                {tableRows.map((r) => {
                  const picked = selected.includes(r.runId);
                  const m = r.s;
                  return (
                    <Box
                      key={r.runId}
                      {...clickable(() => openRun(r), { role: "link" })}
                      sx={{
                        display: "grid",
                        gridTemplateColumns: template,
                        alignItems: "stretch",
                        pl: 2.5,
                        cursor: "pointer",
                        /* The baseline is marked by a rule and a chip, not a filled row. */
                        borderLeft: "2px solid",
                        borderColor:
                          r.runId === baselineRow?.runId
                            ? "primary.main"
                            : "transparent",
                        bgcolor: picked
                          ? (t) => alpha(t.palette.primary.main, 0.05)
                          : "transparent",
                        "&:hover": { bgcolor: "action.hover" },
                        ...focusRing,
                      }}
                    >
                      {/* Selecting a run for Compare never opens it. The whole cell
                      toggles by mouse; Space on the checkbox toggles by keyboard. */}
                      <Box
                        sx={{ display: "flex", alignItems: "center" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.target === e.currentTarget) toggle(r.runId);
                        }}
                      >
                        <Checkbox
                          size="small"
                          checked={picked}
                          onChange={() => toggle(r.runId)}
                          inputProps={{
                            "aria-label": `Select ${r.name || `Trial ${r.n}`}`,
                          }}
                          sx={{
                            p: 0.5,
                            pointerEvents: "none",
                            ...neutralCheckboxSx,
                          }}
                        />
                      </Box>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1.25}
                        sx={{ minWidth: 0, py: 0.875, pr: 1.5 }}
                      >
                        {/* The run's chip — the same R-badge and colour it wears on Compare. */}
                        <Box
                          sx={{
                            minWidth: 30,
                            height: 22,
                            px: 0.75,
                            borderRadius: "4px",
                            flexShrink: 0,
                            display: "grid",
                            placeItems: "center",
                            typography: "s3",
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                            color: r.s?.color
                              ? textTone(r.s.color)
                              : "text.secondary",
                            bgcolor: r.s?.color
                              ? (t) =>
                                  alpha(
                                    r.s.color,
                                    t.palette.mode === "dark" ? 0.22 : 0.14,
                                  )
                              : "background.neutral",
                          }}
                        >
                          {r.s?.letter || `T${r.n}`}
                        </Box>
                        <Box minWidth={0}>
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={0.75}
                            sx={{ minWidth: 0 }}
                          >
                            <Typography
                              noWrap
                              sx={{ typography: "s2", fontWeight: 600 }}
                            >
                              {r.name}
                            </Typography>
                            {r.runId === baselineRow?.runId && (
                              <Chip
                                size="small"
                                label="Baseline"
                                sx={{ flexShrink: 0 }}
                              />
                            )}
                          </Stack>
                          {r.tried && (
                            <Tooltip
                              arrow
                              placement="bottom-start"
                              disableHoverListener={!r.feedback}
                              title={r.feedback ? feedbackText(r.feedback) : ""}
                            >
                              <Typography
                                noWrap
                                sx={{
                                  typography: "s3",
                                  color: "text.subtitle",
                                }}
                              >
                                {r.tried}
                              </Typography>
                            </Tooltip>
                          )}
                        </Box>
                      </Stack>
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          minWidth: 0,
                          pr: 1,
                        }}
                      >
                        {statusOf(r)}
                      </Box>
                      <MetricCell
                        anchor
                        deltaWidth={DELTA}
                        text={`${m?.passRate ?? r.score}%`}
                        delta={
                          baselineRow
                            ? m &&
                              deltaAgainst(metrics.passRate, m, baselineRow.s)
                            : targetDelta(m?.passRate ?? r.score)
                        }
                      />
                      <MetricCell
                        quiet
                        deltaWidth={DELTA}
                        text={
                          m ? `${(m.avgDurationMs / 1000).toFixed(1)}s` : "—"
                        }
                        delta={m && deltaAgainst(metrics.duration, m, baseline)}
                      />
                      <MetricCell
                        quiet
                        deltaWidth={DELTA}
                        text={m ? compactTokens(m.tokens) : "—"}
                        delta={m && deltaAgainst(metrics.tokens, m, baseline)}
                      />
                      <MetricCell
                        quiet
                        deltaWidth={DELTA}
                        text={m ? creditsText(m.cost) : "—"}
                        delta={m && deltaAgainst(metrics.cost, m, baseline)}
                      />
                      <MetricCell
                        quiet={!m?.saidNotDone}
                        deltaWidth={DELTA}
                        text={m ? `${m.saidNotDone}` : "—"}
                        delta={
                          m && deltaAgainst(metrics.saidNotDone, m, baseline)
                        }
                      />
                      <MetricCell
                        anchor
                        deltaWidth={DELTA}
                        text={
                          m?.meanReturn == null ? "—" : m.meanReturn.toFixed(2)
                        }
                        delta={
                          m && deltaAgainst(metrics.meanReturn, m, baseline)
                        }
                      />
                      {evals.map((e, i) => (
                        <ScoreCell
                          key={e.id}
                          value={m?.scores?.[e.id] ?? null}
                          deltaWidth={DELTA}
                          delta={
                            m && metrics[`eval:${e.id}`]
                              ? deltaAgainst(
                                  metrics[`eval:${e.id}`],
                                  m,
                                  baseline,
                                )
                              : null
                          }
                          divider={i === 0}
                          last={i === evals.length - 1}
                        />
                      ))}
                    </Box>
                  );
                })}
                {pendingN && (
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: template,
                      alignItems: "center",
                      pl: 2.5,
                      minHeight: 49,
                    }}
                  >
                    <Box />
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      sx={{ minWidth: 0, pr: 1.5 }}
                    >
                      <Box
                        sx={{
                          minWidth: 30,
                          height: 22,
                          px: 0.75,
                          borderRadius: "4px",
                          display: "grid",
                          placeItems: "center",
                          typography: "s3",
                          fontWeight: 600,
                          color: "text.disabled",
                          bgcolor: "background.neutral",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {`T${pendingN}`}
                      </Box>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s2",
                          fontWeight: 600,
                          color: "text.subtitle",
                        }}
                      >{`Trial ${pendingN}`}</Typography>
                    </Stack>
                    <Stack direction="row" alignItems="center" spacing={0.75}>
                      <Box
                        sx={{
                          width: 10,
                          height: 10,
                          borderRadius: "50%",
                          border: "1.5px solid",
                          borderColor: "primary.main",
                          borderTopColor: "transparent",
                          animation: "runs-spin 0.7s linear infinite",
                          "@keyframes runs-spin": {
                            to: { transform: "rotate(360deg)" },
                          },
                        }}
                      />
                      <Typography
                        noWrap
                        sx={{ typography: "s2", color: "text.subtitle" }}
                      >
                        Running
                      </Typography>
                      {/* Watching is a choice, never a stop on the way. */}
                      <Link
                        component="button"
                        type="button"
                        underline="hover"
                        onClick={() =>
                          navigate(
                            `${paths.dashboard.simulate.simulationRun(env.id, `${record.id}-t${pendingN}`)}?live=1`,
                          )
                        }
                        sx={{
                          typography: "s2",
                          fontWeight: 600,
                          color: "text.primary",
                          ml: 1,
                        }}
                      >
                        Watch live
                      </Link>
                    </Stack>
                  </Box>
                )}
                {running && !runs.length && !pendingN && (
                  <Typography
                    sx={{
                      px: 2.5,
                      py: 2,
                      typography: "s2",
                      color: "text.subtitle",
                    }}
                  >
                    {progress.stage === "reproduce"
                      ? "Trials appear here as they finish. First it checks the failure happens on today's version."
                      : "Trials appear here as they finish."}
                  </Typography>
                )}
              </Stack>
            </Box>
          </Box>

          {/* A coding agent handed this improvement: its attempts, each judged
          the same way as the runs above, under them. */}
          {session && (
            <Box
              sx={{
                mt: 2,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <ImproverAttempts
                record={record}
                env={env}
                envState={envState}
                session={session}
                name={row?.name || record.name}
                searchSpent={result ? searchSpend(result, env.surface) : 0}
              />
            </Box>
          )}
        </>
      )}

      <WinnerDrawer
        open={picking}
        onClose={() => setPicking(false)}
        summaries={field}
        evals={evals}
        scenarioCount={envState?.scenarios?.length || 0}
        env={env}
        envState={envState}
        nameOf={(r) => `Trial ${r.trialN}`}
        showSummary={false}
        onApply={choose}
      />
    </Box>
  );
}

/*
  The improvement's verdict above its chart (RSI PRD, Flow 5), compact: the
  sentence; the numbers side by side — held-back change, target change,
  overfit gap, cost and latency; then the constraints as one line — how many
  passed, any that didn't named in red — with the full checklist a click
  away. Colour only on the change and on a broken constraint.
*/
/* What a retry read from the run before it, and what it changed because of it. */
const feedbackText = (f) =>
  `Fed Trial ${f.from}'s per-scenario results and its ${f.conversations} failing ${f.conversations === 1 ? "conversation" : "conversations"}: ${f.stillFailing.length} still failing (${f.stillFailing.map((x) => `“${x.title.replace(/\.$/, "")}”`).join(", ")}). Adds: “${f.adds}”`;
const signedPct = (n) =>
  `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(Math.round(n * 10) / 10)}%`;
/*
  No safe improvement: every change it tried fixed too little or broke
  something. Three things to try, each with where to do it — never just
  "Fix again", which would repeat the same search.
*/
function NoFixNext({ issueId, onOpenIssue, onOpenRules }) {
  const items = [
    {
      title: "The agent's rules ruled out the change it needs",
      body: "Allowed changes, protected parts or the cost and latency limits can block the fix. Loosen one, then fix again.",
      action: onOpenRules && {
        label: "Open the agent's rules",
        onClick: onOpenRules,
      },
    },
    issueId && {
      title: "It needs a change in the code",
      body: "Tools, orchestration or data aren't changed here. Hand it to your coding agent with the diagnosis — from the issue's Open in Claude Code.",
      action: onOpenIssue && { label: `Open ${issueId}`, onClick: onOpenIssue },
    },
    issueId && {
      title: "Its scenarios miss what triggers it",
      body: "Add scenarios from newer traces or edit them, then Fix again from the issue.",
      action: null,
    },
  ].filter(Boolean);
  return (
    <Box
      sx={{
        mb: 2,
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography sx={{ px: 2, py: 1, typography: "s2", fontWeight: 500 }}>
        What to try next
      </Typography>
      {items.map((it) => (
        <Stack
          key={it.title}
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{ px: 2, py: 1 }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", fontWeight: 500 }}>
              {it.title}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {it.body}
            </Typography>
          </Box>
          {it.action && (
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={it.action.onClick}
              sx={{ flexShrink: 0, borderColor: "divider" }}
            >
              {it.action.label}
            </Button>
          )}
        </Stack>
      ))}
    </Box>
  );
}
NoFixNext.propTypes = {
  issueId: PropTypes.string,
  onOpenIssue: PropTypes.func,
  onOpenRules: PropTypes.func,
};

function Verdict({ outcome, result, targetNow }) {
  const [allChecks, setAllChecks] = useState(false);
  if (!result?.winner && !outcome.headline) return null;
  const thin = !!result?.split?.thin;
  const heldNow = outcome.heldScore ?? result?.heldScore;
  const lift =
    heldNow != null && result?.heldBase != null
      ? heldNow - result.heldBase
      : null;
  const targetLift =
    targetNow != null && result?.base != null ? targetNow - result.base : null;
  const checks = outcome.checks || [];
  const projected = !!outcome.chosen?.projected;
  const effect = outcome.effect || null;
  const cells = [
    /* Hidden when nothing was held back — proven on the targets only. */
    result?.winner &&
      !thin &&
      heldNow != null &&
      result?.heldBase != null && {
        k: "Held back",
        v: thin ? "Too few to measure" : `${result.heldBase}% → ${heldNow}%`,
        sub: projected
          ? "projected"
          : result.confirm
            ? "re-run after the pick"
            : "never seen by the search",
        pts: thin || !lift ? null : lift,
      },
    result?.winner &&
      targetLift != null && {
        k: "Target",
        v: `${result.base}% → ${targetNow}%`,
        sub: "on the scenarios it was tuned on",
        pts: targetLift || null,
      },
    result?.winner &&
      outcome.gap != null &&
      !thin && {
        k: "Overfit gap",
        v: `${outcome.gap} pts`,
        sub: "between target and held back",
      },
    result?.winner &&
      effect && {
        k: "Cost · latency",
        v: `${outcome.costKnown === false ? "—" : signedPct(effect.cost)} · ${signedPct(effect.latency)}`,
        sub:
          outcome.costKnown === false
            ? "cost not measured"
            : "per conversation",
      },
  ].filter(Boolean);
  const failed = checks.filter((c) => c.pass === false);
  const passed = checks.filter((c) => c.pass === true).length;
  const line = { typography: "s3", color: "text.subtitle" };
  return (
    <Box
      sx={{
        mb: 2,
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Typography
        sx={{
          px: 2,
          py: 1,
          typography: "s1",
          fontWeight: 600,
          lineHeight: 1.45,
        }}
      >
        {outcome.headline}
      </Typography>
      {/* What the held-back result is worth, so a count never reads as proof on its own. */}
      {outcome.strength && (
        <Typography
          sx={{
            px: 2,
            mt: -0.75,
            pb: 1,
            typography: "s2",
            color: "text.secondary",
          }}
        >
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
            {outcome.strength.label}
          </Box>
          {` · ${outcome.strength.note.charAt(0).toUpperCase()}${outcome.strength.note.slice(1)}.`}
        </Typography>
      )}

      {/* What else failed today, as the winner left it — a bonus said
          plainly, and what's left to fix separately. Never a target. */}
      {outcome.alongTheWay && (
        <Box sx={{ px: 2, pb: 1, mt: outcome.strength ? -0.5 : -0.75 }}>
          {outcome.alongTheWay.fixed.length > 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box
                component="span"
                sx={{ fontWeight: 600, color: "text.primary" }}
              >
                Also fixed along the way
              </Box>
              {` · ${outcome.alongTheWay.fixed.map((f) => `“${f.title}”`).join(", ")} — failing today, passing with this change. It wasn't aimed at ${outcome.alongTheWay.fixed.length === 1 ? "it" : "them"}.`}
            </Typography>
          )}
          {outcome.alongTheWay.still.length > 0 && (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box
                component="span"
                sx={{ fontWeight: 600, color: "text.primary" }}
              >
                {`Still failing, not part of this fix`}
              </Box>
              {` · ${outcome.alongTheWay.still.length} ${outcome.alongTheWay.still.length === 1 ? "scenario" : "scenarios"} (${outcome.alongTheWay.still
                .slice(0, 2)
                .map((f) => `“${f.title}”`)
                .join(
                  ", ",
                )}${outcome.alongTheWay.still.length > 2 ? "…" : ""}) failed before this fix too — fix ${outcome.alongTheWay.still.length === 1 ? "it" : "them"} separately.`}
            </Typography>
          )}
        </Box>
      )}

      {/* ── the numbers, side by side ── */}
      {cells.length > 0 && (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2, minmax(0, 1fr))",
              md: `repeat(${cells.length}, minmax(0, 1fr))`,
            },
            borderTop: "1px solid",
            borderColor: "divider",
            "& > *": {
              borderColor: "divider",
              borderStyle: "solid",
              borderWidth: 0,
            },
            "& > *:not(:first-of-type)": { borderLeftWidth: 1 },
          }}
        >
          {cells.map((c) => (
            <Box key={c.k} sx={{ px: 2, py: 0.875, minWidth: 0 }}>
              <Typography noWrap sx={line}>
                {c.k}
              </Typography>
              <Stack
                direction="row"
                alignItems="baseline"
                spacing={0.75}
                sx={{ minWidth: 0 }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s1",
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {c.v}
                </Typography>
                {!!c.pts && (
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                      color: c.pts > 0 ? "status.success.fg" : RED,
                    }}
                  >
                    {`${c.pts > 0 ? "▲" : "▼"} ${Math.abs(c.pts)} pts`}
                  </Typography>
                )}
              </Stack>
              <Typography noWrap sx={line}>
                {c.sub}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      {/* ── the constraints: one line, the failures named, the rest on demand ── */}
      {checks.length > 0 && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 2, py: 0.75, minWidth: 0 }}
          >
            <Iconify
              icon={
                failed.length
                  ? "solar:close-circle-bold"
                  : "solar:check-circle-linear"
              }
              width={14}
              sx={{
                color: failed.length ? RED : "text.secondary",
                flexShrink: 0,
              }}
            />
            <Typography
              noWrap
              sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}
            >
              {`Checks · ${passed} of ${checks.length} passed`}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: RED, flex: 1, minWidth: 0 }}
            >
              {failed
                .map((c) => `${c.label}${c.actual ? ` (${c.actual})` : ""}`)
                .join(" · ")}
            </Typography>
            <Button
              size="small"
              onClick={() => setAllChecks((o) => !o)}
              endIcon={
                <Iconify
                  icon={
                    allChecks
                      ? "solar:alt-arrow-up-linear"
                      : "solar:alt-arrow-down-linear"
                  }
                  width={12}
                />
              }
              sx={{ color: "text.secondary", flexShrink: 0, minWidth: 0 }}
            >
              {allChecks ? "Hide" : "Show all"}
            </Button>
          </Stack>
          {allChecks && (
            <Box
              sx={{
                px: 2,
                pb: 1,
                pl: 4.25,
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  md: "repeat(2, minmax(0, 1fr))",
                },
                columnGap: 3,
                rowGap: 0.5,
              }}
            >
              {checks.map((c) => (
                <Stack
                  key={c.id}
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  sx={{ minWidth: 0 }}
                >
                  <Iconify
                    icon={
                      c.pass === null
                        ? "solar:question-circle-linear"
                        : c.pass
                          ? "solar:check-circle-linear"
                          : "solar:close-circle-bold"
                    }
                    width={14}
                    sx={{
                      color:
                        c.pass === false
                          ? RED
                          : c.pass === null
                            ? "text.disabled"
                            : "text.secondary",
                      flexShrink: 0,
                    }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      color: c.pass === false ? RED : "text.primary",
                      flexShrink: 0,
                    }}
                  >
                    {c.label}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      minWidth: 0,
                    }}
                  >
                    {c.actual}
                  </Typography>
                </Stack>
              ))}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}
Verdict.propTypes = {
  outcome: PropTypes.object.isRequired,
  result: PropTypes.object,
  targetNow: PropTypes.number,
};

/*
  The credit ledger: spent so far, reserved for work still running (released
  when it ends), committed (spent plus reserved) and the budget.
*/
function CreditLedger({ spent, budget, running }) {
  const reserved = running && budget ? Math.max(0, budget - spent) : 0;
  const cells = [
    { k: "Spent", v: spent },
    { k: "Reserved", v: reserved },
    { k: "Committed", v: spent + reserved },
    { k: "Budget", v: budget ?? null },
  ];
  return (
    <Stack
      direction="row"
      sx={{
        mb: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      {cells.map((c, i) => (
        <Box
          key={c.k}
          sx={{
            flex: 1,
            px: 1.5,
            py: 1,
            ...(i > 0 && { borderLeft: "1px solid", borderColor: "divider" }),
          }}
        >
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {c.k}
          </Typography>
          <Typography
            sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
          >
            {creditsLabel(c.v)}
          </Typography>
        </Box>
      ))}
    </Stack>
  );
}

CreditLedger.propTypes = {
  spent: PropTypes.number,
  budget: PropTypes.number,
  running: PropTypes.bool,
};

ImprovementRunsView.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  record: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
  backLabel: PropTypes.string,
  onOpenSearch: PropTypes.func,
  /* Runs the chosen winner on the full suite. */
  onVerify: PropTypes.func,
  /* Opens the hand-over for a verified winner. */
  onShip: PropTypes.func,
  /* Re-run the kept change on today's production version. */
  onRecheck: PropTypes.func,
  /* Running: from improvementProgressOf — the stage and runs finished so far. */
  progress: PropTypes.object,
  onOpenIssue: PropTypes.func,
  /* The run it started from, when it started from one. */
  onOpenStartRun: PropTypes.func,
};
