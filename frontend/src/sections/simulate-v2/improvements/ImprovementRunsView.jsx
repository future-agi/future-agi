import PropTypes from "prop-types";
import { requiredEvalIds } from "../_mock/evals";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  Chip,
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
import EpochScenarioResults from "./EpochScenarioResults";
import { membersOf, proposalMembers } from "../_mock/improvementPolicy";
import {
  improvementOutcome,
  IMPROVEMENT_PHASES,
  OPT_STATUS,
  runsCountOf,
  searchSpend,
  improvementTotalSpend,
  stopImprovement,
  requestStop,
} from "../_mock/optimizationRuns";
import CostPartsTable from "./CostPartsTable";
import { downloadExperiment } from "../_mock/experimentExport";
import RaiseLimitBar from "./RaiseLimitBar";
import { creditsForUsd, creditsLabel } from "../_mock/metering";
import { RATE_PLAN } from "../_mock/rates";
import RunsOverview from "../run/RunsOverview";
import WinnerDrawer from "../run/WinnerDrawer";
import { improvementRowsOf } from "./improvementRows";
import {
  chartRunOf,
  creditsWord,
  epochSpendOf,
  trialRowsFor,
} from "./improvementRunsData";
import StatusPill from "./ImprovementStatusPill";
import {
  agentDefinitionOf,
  agentIdOf,
  envAgentKey,
} from "../_mock/agentRegistry";
import { behindProductionOf } from "./behindProduction";
import ImprovementStages from "./ImprovementStages";
import ClosableDialog from "../components/ClosableDialog";
import { diagnosisSummaryOf, improvementRunName } from "../_mock/optimizer";
import { SegmentedTabs } from "src/components/tabs/tabs";
import EpochVerdict, { DiagnosisLine } from "./EpochVerdict";
import EpochCandidatesTable from "./EpochCandidatesTable";
import CandidateChangeDrawer from "./CandidateChangeDrawer";
import {
  candidateGroupsOf,
  candidateRowsOf,
  changeSummaryOf,
  evidenceRowsOf,
} from "./epochCandidates";
import EpochEnding from "./EpochEnding";
import EpochMoreMenu from "./EpochMoreMenu";
import EpochSearchDetails from "./EpochSearchDetails";
import IssueScenarioEditor from "./IssueScenarioEditor";
import NewImprovement from "./NewImprovement";
import {
  ENDINGS_WITH_CANDIDATES,
  earlyEndingOf,
  endingCopyOf,
} from "./epochEndings";
import { epochBriefOf, epochProofOf } from "./epochBrief";
import { downloadPatch, patchTextOf } from "./epochShip";
import { proofDisplay } from "../run/fixmyagent/proofBlock";
import { ReceiptButton } from "../run/fixmyagent/ProofParts";
import { ContractNotice } from "../run/fixmyagent/ImprovementPanels";
import {
  contractApprovalPatch,
  issueDraftsOf,
  nextContractVersion,
  reproduceAgainPatch,
} from "../run/fixmyagent/improvementView";
import {
  snapshotByLabel,
  snapshotName,
  snapshotOfImprovement,
} from "../_mock/envSnapshot";
import { envLabelOf } from "../_mock/envVersionKinds";
import { currentEnvVersion } from "../_mock/versions";

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
  onVerify,
  onShip,
  onRecheck,
  progress = null,
  onOpenIssue,
  onOpenStartRun,
  tabAsk = null,
  onTabAsked,
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
  /* The epoch's ID — what fi api epochs get / promote and the SDK take,
     so code reads this same epoch. */
  const experimentId = `${env.id}/${record.id}`;
  const command = `fi api epochs get ${experimentId} --json`;
  const copyText = (text) =>
    navigator.clipboard?.writeText(text).catch(() => {});
  const [receiptOpen, setReceiptOpen] = useState(false);
  /* Run Epoch again, with this agent — the setup drawer. */
  const [againOpen, setAgainOpen] = useState(false);
  /* Not reproduced: the issue's scenarios, edited in place. */
  const [editingScenarios, setEditingScenarios] = useState(false);
  /* Stop: asked first, with what has run and what it has cost so far. */
  const [stopAsk, setStopAsk] = useState(false);
  const stopNow = () => {
    setStopAsk(false);
    /* Stopping first (improve.md): it settles in-flight work, then the
       stop records what finished. */
    const mark = (fn) =>
      patch({
        optimizations: (envState?.optimizations || []).map((o) =>
          o.id === record.id ? fn(o) : o,
        ),
      });
    mark((o) => requestStop(o));
    setTimeout(() => mark((o) => stopImprovement(o, progress)), 1500);
  };
  /* Flow 5's tabs once it has finished: the runs, what the kept change
     changes (each with its reason), and what it did to each scenario. */
  const [tab, setTab] = useState(null);
  /* Asked from outside (Ship's See changes): open that tab once. */
  useEffect(() => {
    if (!tabAsk) return;
    setTab(tabAsk);
    onTabAsked?.();
  }, [tabAsk]); // eslint-disable-line react-hooks/exhaustive-deps

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
  /* Only a verified (or limited-validation) candidate ships — never the
     least-bad of a search that didn't verify. */
  const shippable =
    ["ready", "stopped-early", "budget"].includes(outcome.ending.key) &&
    outcome.strength?.key !== "none" &&
    record.status !== OPT_STATUS.OVER_BUDGET &&
    !!record.result?.winner;
  const result = record.result;
  /* What ships — the search's pick or the run someone chose — and which
     members of a multi-agent its changes touch. */
  const shipped = shippedCandidate(record) || result?.winner || null;
  /* Only an agent with more than one member says which one a change lands on. */
  const members = useMemo(() => {
    const ctx = {
      agent: envState?.agent || null,
      limits: record.limits,
      target: record.targetMember,
    };
    return membersOf(ctx.agent).length > 1
      ? proposalMembers(shipped?.proposals || [], ctx)
      : null;
  }, [shipped, envState, record.limits, record.targetMember]);
  /* A stopped one has no kept change and no held-back results: its trials
     and what it cost, nothing to choose. */
  const stopped = outcome?.ending?.key === "stopped";
  /* Ended early — stopped, no safe change, not reproduced, too few, over
     budget: one ending layout (EpochEnding) instead of the verdict. */
  const ending = running ? null : earlyEndingOf(record, outcome);
  /* Tabs once there are candidates to read; an ending with none is the
     ending alone. */
  const tabbed =
    !running &&
    !!result?.trials?.length &&
    (!ending || ENDINGS_WITH_CANDIDATES.has(ending));
  /* What it spent — one sum, read by the ending line and the Cost tab. */
  const spend = useMemo(
    () => epochSpendOf(record, env?.surface),
    [record, env?.surface],
  );
  /* A winner to lead with: kept, and an ending that has one to review. */
  const hasWinner = !running && !!kept && !ending;
  /* Every candidate, at the step where the epoch stopped it. */
  const candidateRows = useMemo(
    () =>
      running
        ? []
        : candidateRowsOf(record, {
            rows: allRuns,
            candidates,
            keptN,
            hasWinner,
            stopped,
          }),
    [running, record, allRuns, candidates, keptN, hasWinner, stopped],
  );
  const candidateGroups = useMemo(
    () => candidateGroupsOf(candidateRows, { stopped, hasWinner }),
    [candidateRows, stopped, hasWinner],
  );
  const winnerRow = candidateRows.find((c) => c.stage === "winner") || null;
  const alsoPassed = candidateRows
    .filter((c) => c.stage === "passed")
    .sort((a, b) => b.score - a.score || a.n - b.n);
  /* No change to show — stopped, or nothing safe was found: the candidates
     (the rejected ones included) are where to look. */
  const noChanges = stopped || ending === "no-fix";
  /* Opens on the first tab — Changes — unless it has none to show. */
  const view = tabbed
    ? tab || (noChanges ? "candidates" : "changes")
    : running
      ? "runs"
      : "none";
  /* The Candidates tab: grouped by where the epoch stopped each one, or
     every measurement side by side (the chart, Compare, Choose winner). */
  const [candidatesMode, setCandidatesMode] = useState("result");
  const measurements =
    view === "runs" || (view === "candidates" && candidatesMode === "all");
  /* One candidate's change, in a drawer beside the page. */
  const [changeOf, setChangeOf] = useState(null);
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
  /* Live already (released, watched, resolved or reopened): nothing left
     to re-check before it ships. */
  const live = ["released", "reopened"].includes(row?.status?.key);
  /* Behind and not re-checked: Ship waits for the re-check. */
  const holdShip = !!behind && !behind.recheck && !live;
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
            label: row?.name || "the epoch",
            state: location.state,
          },
        },
      },
    );
  const openRun = (r) =>
    navigate(paths.dashboard.simulate.simulationRun(env.id, r.runId), {
      state: { back: { to: `${location.pathname}${location.search}` } },
    });

  /* ── the ways on from an early ending ── */
  const markRecord = (fn) =>
    patch({
      optimizations: (envState?.optimizations || []).map((o) =>
        o.id === record.id ? fn(o) : o,
      ),
    });
  /* Stopped on our side: the same epoch again, from now — once (Kartik, #77). */
  const retry = () =>
    markRecord((o) => ({
      ...o,
      status: OPT_STATUS.RUNNING,
      startedAt: new Date().toISOString(),
      result: o.result || o.pendingResult,
      pendingResult: undefined,
      pendingEnding: undefined,
      retries: (o.retries || 0) + 1,
    }));
  /* Not reproduced → a diagnosis and a suggested change, no candidates. */
  const diagnoseOnly = () =>
    markRecord((o) => ({
      ...o,
      status: OPT_STATUS.RUNNING,
      startedAt: new Date().toISOString(),
      fixDepth: "suggestion",
      result: {
        ...(o.result || o.pendingResult),
        winner: null,
        finalists: [],
        trials: [],
      },
      pendingResult: undefined,
      pendingEnding: undefined,
      reproduce: { ...o.reproduce, diagnosticOnly: true },
    }));
  /* A new epoch: an issue's starts from its issue (Run Epoch there), any
     other from the setup drawer with this agent picked. */
  const runAgain = () => {
    if (record.trigger?.issueId && onOpenIssue) onOpenIssue();
    else setAgainOpen(true);
  };
  const openScenarios = () =>
    navigate(paths.dashboard.simulate.environmentStep(env.id, "scenarios"));
  const openRules = envState?.agent
    ? () =>
        navigate(
          `${paths.dashboard.agents}/connected/${agentIdOf(envAgentKey(envState.agent, env.id))}?tab=constraints`,
        )
    : null;
  /* What the epoch set out to fix, for the Diagnosis line every finished
     epoch keeps (a winner's verdict and a failed ending alike). */
  const epochDiagnosis = (() => {
    /* An issue's entry carries the cause as its headline; a finding from
       Debug failures carries its name as the label and only a count
       ("1 gaming signal") as the headline — so it reads by its name. */
    const wordOf = (x) =>
      String(
        (x?.source === "debug-failures"
          ? x?.label || x?.headline
          : x?.headline || x?.label) || "",
      ).replace(/\.$/, "");
    const d = diagnosisSummaryOf(record, result);
    const items = (record.diagnosis || []).map(wordOf).filter(Boolean);
    const cause = (
      record.snapshot?.diagnosis?.cause ||
      record.snapshot?.rootCause ||
      items[0] ||
      d?.cause ||
      ""
    ).replace(/\.$/, "");
    if (!cause) return null;
    const more = items.filter((x) => x !== cause);
    return { cause, more: [...new Set(more)] };
  })();
  const endingCopy = ending
    ? endingCopyOf(ending, {
        record,
        outcome,
        name: row?.name || record.name,
        credits: spend.credits,
      })
    : null;
  /* One primary and at most two links per ending. */
  const endingActions = (() => {
    if (ending === "failed")
      return {
        primary:
          outcome.canRetry !== false
            ? { label: `Retry ${row?.name || "the epoch"}`, onClick: retry }
            : { label: "Run Epoch again", onClick: runAgain },
      };
    if (ending === "stopped")
      return { primary: { label: "Run Epoch again", onClick: runAgain } };
    if (ending === "not-reproduced")
      return {
        primary: issueDraftsOf(record).length
          ? {
              label: "Edit scenarios and reproduce again",
              onClick: () => setEditingScenarios(true),
            }
          : null,
        links: [
          { label: "Diagnose only", onClick: diagnoseOnly },
          {
            label: "Connect a source",
            onClick: () =>
              navigate(
                paths.dashboard.simulate.environmentStep(env.id, "world"),
              ),
          },
        ],
      };
    if (ending === "too-few" || ending === "nothing")
      return { primary: { label: "Add scenarios", onClick: openScenarios } };
    return {};
  })();
  /* The ⋯ menu: everything that isn't the page's one primary. */
  const moreItems = [
    {
      label: "Copy epoch ID",
      hint: experimentId,
      icon: "solar:copy-linear",
      done: "Copied",
      onClick: () => copyText(experimentId),
    },
    {
      label: "Copy command",
      hint: command,
      icon: "solar:code-linear",
      done: "Copied",
      onClick: () => copyText(command),
    },
    !running && {
      label: "Export epoch",
      hint: "Baseline, scenarios, changes, candidates, result and spend — no credentials.",
      icon: "solar:download-minimalistic-linear",
      onClick: () => downloadExperiment(record, env),
    },
    !running &&
      (shipped?.proposals || []).length > 0 &&
      !noChanges && {
        label: "Download patch",
        hint: "The winner's changes, file by file.",
        icon: "solar:download-minimalistic-linear",
        onClick: () =>
          downloadPatch(
            `${env.name} ${row?.name || record.name}`,
            patchTextOf(env, shipped.proposals),
          ),
      },
    !running &&
      result && {
        label: "Receipt",
        hint: "What ran, on which versions, and its seal.",
        icon: "solar:document-text-linear",
        onClick: () => setReceiptOpen(true),
      },
    !running &&
      result && {
        label: "Copy for coding agent",
        hint: "A brief with the issue, what the search found, what still fails and the constraints.",
        icon: "solar:programming-linear",
        done: "Copied",
        onClick: () =>
          copyText(
            epochBriefOf({
              record,
              env,
              envState,
              state: simState,
              name: row?.name || record.name,
            }),
          ),
      },
  ];
  /* The environment version it was frozen on, and a contract approval's. */
  const frozenOn = snapshotName(
    snapshotOfImprovement(record, env, envState),
    true,
  );
  const ca = record.contractApproval;
  const shownApproval = ca
    ? {
        ...ca,
        to:
          (ca.toSnapshot
            ? snapshotByLabel(env, envState, ca.toSnapshot)?.envLabel
            : null) || envLabelOf(env, envState, ca.to),
        from:
          (ca.fromSnapshot
            ? snapshotName(
                snapshotByLabel(env, envState, ca.fromSnapshot),
                true,
              )
            : null) ||
          frozenOn ||
          ca.from,
      }
    : ca;
  const proofLine =
    proofDisplay(epochProofOf(record, envState?.agent || null))?.line || null;
  /* Shipped: Ship opens its steps where it stands. */
  const shippedKind = row?.output?.kind || null;
  const shipLabel = !shippedKind
    ? `Ship ${kept?.name || ""}`.trim()
    : ["draft", "version"].includes(shippedKind)
      ? "Continue shipping"
      : "View shipping";

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
              ? "The search kept this candidate — verified on the held-back scenarios."
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
              {/* Where it lives: its environment and that environment's
                  Epochs tab — never the issue or cluster it was started
                  from (the back arrow still returns there). */}
              {[
                {
                  label: env.name,
                  to: paths.dashboard.simulate.environmentDetail(env.id),
                },
                {
                  label: "Epochs",
                  to: `${paths.dashboard.simulate.environmentDetail(env.id)}/improvements`,
                },
              ].map((c) => (
                <Stack
                  key={c.label}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ minWidth: 0, flexShrink: c.label === "Epochs" ? 0 : 1 }}
                >
                  <Link
                    component="button"
                    type="button"
                    underline="hover"
                    onClick={() => navigate(c.to)}
                    noWrap
                    sx={{
                      typography: "s2",
                      color: "text.tertiary",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      minWidth: 0,
                      "&:hover": { color: "text.primary" },
                    }}
                  >
                    {c.label}
                  </Link>
                  <Typography
                    component="span"
                    sx={{ typography: "s2", color: "text.disabled" }}
                  >
                    /
                  </Typography>
                </Stack>
              ))}
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
            <Tooltip
              arrow
              placement="bottom-start"
              title="The epoch's ID — the same in the CLI and API. Starting the same epoch again opens this one."
            >
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
                  /* The environment is in the crumb above. */
                  row?.identity,
                  /* The issue it fixes, by name — not just its ID. */
                  record.trigger?.issueId
                    ? `${record.trigger.issueId} ${mockCluster(record.trigger.issueId)?.error?.name || ""}`.trim()
                    : row?.trigger?.label,
                  record.id,
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
              <ClosableDialog
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
                    {`${progress.runsDone} of up to ${progress.runsMax || progress.runsTotal} candidates ${progress.runsDone === 1 ? "has" : "have"} run · ${creditsLabel(improvementTotalSpend(stopImprovement(record, progress), env.surface))} spent so far.`}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary", mt: 1 }}
                  >
                    None of them has been checked on the held-back scenarios, so
                    nothing can be chosen — today&apos;s version stays. The
                    candidates that ran stay on this page; nothing more is
                    spent.
                  </Typography>
                </DialogContent>
                <DialogActions sx={{ px: 3, pb: 2.5 }}>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={() => setStopAsk(false)}
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
              </ClosableDialog>
            </>
          )}
          {/* Copy ID, Copy command, Export, Receipt, Copy for coding agent —
              everything that isn't the page's one primary. */}
          <EpochMoreMenu items={moreItems} />
          {/* The winner's own run opens from the winner block below. */}
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
            ) : onShip && row?.output?.kind === "pr" ? (
              /* Its pull request is open: Ship shows it. */
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={onShip}
                startIcon={
                  <Iconify icon="solar:git-pull-request-linear" width={14} />
                }
                sx={{ flexShrink: 0 }}
              >
                View pull request
              </Button>
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
              <Tooltip
                arrow
                describeChild
                title={`Runs it again on ${behind.prod}, the version in production now — its proof is on an older one.`}
              >
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={onRecheck}
                  sx={{ flexShrink: 0 }}
                >
                  Re-check
                </Button>
              </Tooltip>
            ) : onShip ? (
              <Tooltip
                arrow
                title={
                  shippedKind
                    ? ""
                    : `Opens the steps that ship ${kept.name} where ${envState?.agent?.name || "the agent"} runs. Nothing goes live until someone makes it live there.`
                }
              >
                <Button
                  size="small"
                  variant={
                    shipLabel === "View shipping" ? "outlined" : "contained"
                  }
                  color="primary"
                  onClick={onShip}
                  startIcon={
                    <Iconify
                      icon="solar:upload-minimalistic-linear"
                      width={14}
                    />
                  }
                  sx={{
                    flexShrink: 0,
                    /* Nothing left to do: a quiet button, not the primary. */
                    ...(shipLabel === "View shipping" && {
                      color: "text.primary",
                      borderColor: "divider",
                    }),
                  }}
                >
                  {shipLabel}
                </Button>
              </Tooltip>
            ) : null)}
        </Stack>
      </Box>

      {rebaseFirst && (
        <ClosableDialog
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
              {`Accept to re-check the rebased patch on ${behind.prod}; the proof on ${behind.base} doesn't carry over.`}
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
        </ClosableDialog>
      )}

      {/* Fell behind production while it waited — only when something can
          still ship; the header's Re-check is the way on. */}
      {behind && !running && hasWinner && shippable && !live && (
        <EpochEnding
          label={
            behind.recheck
              ? `Re-checked on ${behind.prod}`
              : rebaseFirst
                ? "Needs a rebase"
                : "Needs re-check"
          }
          sentence={
            behind.git
              ? `main has moved on ${behind.since} commit${behind.since === 1 ? "" : "s"} since this epoch started.`
              : `${behind.prod} is in production now — ${behind.since} version${behind.since === 1 ? "" : "s"} after this epoch started.`
          }
          spent={
            behind.recheck
              ? `Re-checked on ${behind.prod}${recheckRun ? ` in Run ${recheckRun.ordinal}${recheckRun.finishedAt ? "" : " (still running)"}` : ""} — compare it with its proof on ${behind.base} before it ${behind.git ? "is merged" : "ships"}.`
              : rebaseFirst
                ? `Its patch conflicts with those commits in ${behind.conflict.path}. Review the rebase onto ${behind.prod} first — nothing is applied until you accept it — then it's re-checked there.`
                : behind.git
                  ? `Its patch is rebased onto ${behind.prod}${behind.conflict ? ` (${behind.conflict.path} merged by you)` : ""} and re-checked there before it is merged — its proof is on ${behind.base}.`
                  : `Its proof is on ${behind.base}, so it's re-checked on ${behind.prod} before it ships.`
          }
          /* One line: how it's handled is on hover — unless a conflict
             needs the reader's attention. */
          spentOnHover={!rebaseFirst && !behind.recheck}
        />
      )}

      {/* Winner first: one sentence, one line of checks, then the winner
          and the others that passed. While it runs, the stage it's in takes
          that place; an epoch that ended early reads as its ending. */}
      {running ? (
        <ImprovementStages
          record={record}
          envState={envState}
          progress={progress}
          model={agentModel}
          onOpenIssue={onOpenIssue}
          onOpenRun={onOpenStartRun}
        />
      ) : ending && endingCopy ? (
        <EpochEnding
          label={endingCopy.label}
          danger={endingCopy.danger}
          sentence={endingCopy.sentence}
          spent={endingCopy.spent}
          primary={endingActions.primary}
          links={endingActions.links}
        >
          <DiagnosisLine diagnosis={epochDiagnosis} />
          {ending === "no-fix" ? (
            /* Never a dead end: what to try instead, one way on each. */
            <NextSteps
              issueId={record.trigger?.issueId || null}
              onOpenIssue={onOpenIssue}
              onOpenRules={openRules}
            />
          ) : ending === "over-budget" && patch && record.pendingResult ? (
            /* Raise it here and continue — the inline raise is the way on. */
            <Box sx={{ px: 2, py: 1 }}>
              <RaiseLimitBar
                record={record}
                env={env}
                envState={envState}
                patch={patch}
                framed={false}
              />
            </Box>
          ) : null}
        </EpochEnding>
      ) : (
        <EpochVerdict
          outcome={outcome}
          result={result}
          winner={hasWinner ? winnerRow : null}
          winnerNote={
            kept && keptN !== searchWinner
              ? needsVerify
                ? "your pick · not verified yet"
                : "your pick"
              : null
          }
          changeSummary={changeSummaryOf(env, shipped?.proposals || [])}
          alsoPassed={hasWinner ? alsoPassed : []}
          evidence={
            hasWinner
              ? evidenceRowsOf(outcome, result, winnerRow, proofLine)
              : []
          }
          diagnosis={epochDiagnosis}
          onSeeDiff={noChanges ? undefined : () => setTab("changes")}
          onSeeChange={setChangeOf}
          onOpenRun={openRun}
          onShowCandidates={() => {
            setCandidatesMode("result");
            setTab("candidates");
          }}
        />
      )}

      {/* Not reproduced: the issue's scenarios, edited, then reproduced again. */}
      {ending === "not-reproduced" && patch && (
        <IssueScenarioEditor
          open={editingScenarios}
          drafts={issueDraftsOf(record)}
          perScenario={record.reproduce?.perScenario}
          onClose={() => setEditingScenarios(false)}
          onReproduce={(edits) => {
            const next = reproduceAgainPatch({
              record,
              env,
              envState,
              state: simState,
              edits,
            });
            if (next) patch(next);
            setEditingScenarios(false);
          }}
        />
      )}

      {/* Stopped partway at its budget or time cap: raise it here and continue. */}
      {!running && !ending && record.stoppedAtBudget?.trials > 0 && (
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
            boxShadow: "none",
            mb: 2,
            flexShrink: 0,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {/* Changes / Scenarios / Candidates (every one the epoch tried,
              and what each measured), plus measured cost. No safe change
              has no Changes: its rejected tries are under Candidates. */}
          {!noChanges && <Tab value="changes" label="Changes" />}
          {!stopped && <Tab value="scenarios" label="Scenarios" />}
          <Tab
            value="candidates"
            label={
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <span>Candidates</span>
                <Typography
                  component="span"
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {candidateRows.length}
                </Typography>
              </Stack>
            }
          />
          <Tab value="cost" label="Cost" />
        </Tabs>
      )}

      {/* Scenarios: the environment's own Scenarios table, read only, with
          the epoch's result per scenario — and the scenarios it wrote, to
          add to the environment. */}
      {view === "scenarios" && (
        <Box sx={{ flexShrink: 0 }}>
          <EpochScenarioResults
            env={env}
            envState={envState}
            record={record}
            epochName={improvementRunName(envState, record)}
            patch={patch}
          />
        </Box>
      )}

      {/* Candidates: grouped by where the epoch stopped each one, or every
          measurement side by side. The pick rule, said once. */}
      {view === "candidates" && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ mb: 1.5, flexShrink: 0, minHeight: 30 }}
        >
          <SegmentedTabs
            value={candidatesMode}
            onChange={(_, v) => setCandidatesMode(v)}
            aria-label="Show candidates"
          >
            <Tab value="result" label="By result" />
            <Tab value="all" label="All measurements" />
          </SegmentedTabs>
          <Box flex={1} />
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              textAlign: "right",
            }}
          >
            Finalists are scored once on held-back scenarios; of those that
            pass, the highest target score is kept. Only the winner ships.
          </Typography>
        </Stack>
      )}
      {/* A change the frozen environment can't prove: approve the contract. */}
      {view === "candidates" && result?.contract && (
        <Box sx={{ mx: -2.5, mt: -2.25, mb: 1.5, flexShrink: 0 }}>
          <ContractNotice
            contract={result.contract}
            envVersion={shownApproval?.from || frozenOn}
            nextVersion={nextContractVersion(env, envState)}
            approval={shownApproval}
            onApprove={() =>
              patch(
                contractApprovalPatch({
                  env,
                  envState,
                  record,
                  from: currentEnvVersion(env, envState)?.label || null,
                }),
              )
            }
          />
        </Box>
      )}
      {view === "candidates" && candidatesMode === "result" && (
        <Box sx={{ flexShrink: 0 }}>
          <EpochCandidatesTable
            key={record.id}
            groups={candidateGroups}
            onSeeChange={setChangeOf}
          />
        </Box>
      )}

      {view === "cost" && (
        <Box
          sx={{
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            p: 2,
          }}
        >
          {/* Reserved and committed mean something only while it runs. */}
          {running && (
            <CreditLedger
              spent={spend.credits}
              budget={record.budget}
              reserved={record.reservedCredits}
            />
          )}
          {/* What this epoch spent, part by part, on the versions in its
              header; a stop at the budget keeps what it found. */}
          <CostPartsTable
            parts={spend.parts}
            totalLabel={
              record.budget
                ? `Spent of ${creditsLabel(record.budget)} budget`
                : "Spent"
            }
            note={[
              record.stoppedAtBudget
                ? "Stopped at its budget — the best it found is kept"
                : null,
              `${RATE_PLAN.name} · ${RATE_PLAN.version} rates`,
              "each part charged once — its candidates' own Cost tabs list their calls",
              record.budget ? "the budget is for the whole epoch" : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          />
        </Box>
      )}

      {view === "changes" && (
        <Box
          sx={{
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          <WinnerChanges
            env={env}
            winner={shipped}
            record={record}
            hideCause={!!epochDiagnosis}
            members={members}
            patchName={`${env.name} ${row?.name || record.name}`}
          />
        </Box>
      )}

      {measurements && chartRuns.length > 0 && (
        <Box sx={{ mb: 2, flexShrink: 0 }}>
          <RunsOverview
            runs={chartRuns}
            unit="candidate"
            evals={[]}
            headline={headline}
            onOpenRun={(r) =>
              navigate(paths.dashboard.simulate.simulationRun(env.id, r.id), {
                state: {
                  back: { to: `${location.pathname}${location.search}` },
                },
              })
            }
          />
        </Box>
      )}

      {measurements && (
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
                      ? `Candidates (${runs.length}) · ${c.stopped}`
                      : `Candidates (${runs.length} of ${c.upTo ? "up to " : ""}${c.max})`}
                  </Typography>
                );
              })()}
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
            >
              {running && progress.stage === "reproduce" ? (
                "Each candidate is the agent with one change, simulated on this environment — open one to see its conversations"
              ) : selected.length ? (
                `${selected.length} selected`
              ) : baselineRow ? (
                <>
                  Measured against{" "}
                  <Box
                    component="span"
                    sx={{ color: "text.primary", fontWeight: 600 }}
                  >
                    {baselineRow.name || `Candidate ${baselineRow.n}`}
                  </Box>{" "}
                  — rates in points, everything else in percent
                </>
              ) : (
                `Select two or more to compare · pass rates are on the target scenarios, against ${result?.base ?? "—"}% before the change${startRun ? `; other columns against ${env.name} Run ${startRun.ordinal}` : ""}`
              )}
            </Typography>
            {selected.length > 0 && (
              <Button
                variant="outlined"
                size="small"
                onClick={() => setSelected([])}
                sx={{ minWidth: 0 }}
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
                variant="outlined"
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
                    ? "Once every candidate has finished and been checked."
                    : field.length < 2
                      ? "Fewer than two candidates passed the checks — there's nothing to choose between."
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
                {/* RSI's attempts are candidates; a trial is one scenario attempt. */}
                <Head>Candidate</Head>
                <Head>Status</Head>
                <Head
                  right
                  title="Scenarios this candidate passed, on the target scenarios it was scored on"
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
                            "aria-label": `Select ${r.name || `Candidate ${r.n}`}`,
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
                      >{`Candidate ${pendingN}`}</Typography>
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
                      ? "Candidates appear here as they finish. First it checks the failure happens on today's version."
                      : "Candidates appear here as they finish."}
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

      {/* How it searched and what it was measured against — collapsed. */}
      {view === "candidates" && (
        <EpochSearchDetails
          record={record}
          model={agentModel}
          identity={row?.identity || null}
          envSnapshot={frozenOn}
        />
      )}

      {/* Opened from the ⋯ menu. */}
      {result && (
        <ReceiptButton
          open={receiptOpen}
          onClose={() => setReceiptOpen(false)}
          record={record}
          outcome={outcome}
          identity={row?.identity || null}
          envSnapshot={frozenOn}
        />
      )}
      <NewImprovement
        open={againOpen}
        initialAgentId={
          envState?.agent
            ? agentIdOf(envAgentKey(envState.agent, env.id))
            : null
        }
        onClose={() => setAgainOpen(false)}
      />

      <CandidateChangeDrawer
        candidate={changeOf}
        env={env}
        record={record}
        onClose={() => setChangeOf(null)}
        onOpenRun={(c) => {
          setChangeOf(null);
          openRun(c);
        }}
      />

      <WinnerDrawer
        open={picking}
        onClose={() => setPicking(false)}
        summaries={field}
        evals={evals}
        scenarioCount={envState?.scenarios?.length || 0}
        env={env}
        envState={envState}
        nameOf={(r) => `Candidate ${r.trialN}`}
        showSummary={false}
        onApply={choose}
      />
    </Box>
  );
}

/* What a retry read from the run before it, and what it changed because of it. */
const feedbackText = (f) =>
  `Fed Candidate ${f.from}'s results (${f.conversations} failing ${f.conversations === 1 ? "conversation" : "conversations"}): ${f.stillFailing.length} still failing (${f.stillFailing.map((x) => `“${x.title.replace(/\.$/, "")}”`).join(", ")}). Adds: “${f.adds}”`;
/*
  No safe change: every change it tried fixed too little or broke
  something. Three things to try, as one table — the first that applies
  is the one button, the others are links. Never just "Run Epoch again",
  which would repeat the same search.
*/
function NextSteps({ issueId, onOpenIssue, onOpenRules }) {
  const items = [
    {
      title: "The constraints ruled out the change it needs",
      body: "Allowed changes, must-pass parts or the cost and reply time limits can block the change. Loosen one, then run the epoch again.",
      action: onOpenRules && {
        label: "Open its constraints",
        onClick: onOpenRules,
      },
    },
    issueId && {
      title: "It needs a change in the code",
      body: "Tools, orchestration and data aren't changed here. Hand it to your coding agent from the issue.",
      action: onOpenIssue && { label: `Open ${issueId}`, onClick: onOpenIssue },
    },
    issueId && {
      title: "Its scenarios miss what triggers it",
      body: "Add scenarios from newer traces or edit them, then Run Epoch again from the issue.",
      action: null,
    },
  ].filter(Boolean);
  const first = items.findIndex((it) => it.action);
  return (
    <Box
      sx={{ "& > * + *": { borderTop: "1px solid", borderColor: "divider" } }}
    >
      <Typography
        sx={{
          px: 2,
          py: 0.75,
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
        }}
      >
        What to try next
      </Typography>
      {items.map((it, i) => (
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
          {it.action &&
            (i === first ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={it.action.onClick}
                sx={{ flexShrink: 0 }}
              >
                {it.action.label}
              </Button>
            ) : (
              <Link
                component="button"
                type="button"
                underline="hover"
                onClick={it.action.onClick}
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  color: "text.primary",
                  flexShrink: 0,
                }}
              >
                {it.action.label}
              </Link>
            ))}
        </Stack>
      ))}
    </Box>
  );
}
NextSteps.propTypes = {
  issueId: PropTypes.string,
  onOpenIssue: PropTypes.func,
  onOpenRules: PropTypes.func,
};

/*
  The credit ledger: spent so far, reserved for work still running (released
  when it ends), committed (spent plus reserved) and the budget. Reserved is
  what the service reports — never budget minus spent.
*/
function CreditLedger({ spent, budget, reserved }) {
  /* Spent is in credits (epochSpendOf); the rest are what the service reports. */
  const reservedCredits = reserved == null ? null : creditsForUsd(reserved);
  const cells = [
    { k: "Spent", v: creditsWord(spent) },
    { k: "Reserved", v: creditsWord(reservedCredits) },
    {
      k: "Committed",
      v: creditsWord(reservedCredits == null ? null : spent + reservedCredits),
    },
    { k: "Budget", v: creditsLabel(budget ?? null) },
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
            {c.v}
          </Typography>
        </Box>
      ))}
    </Stack>
  );
}

CreditLedger.propTypes = {
  reserved: PropTypes.number,
  spent: PropTypes.number,
  budget: PropTypes.number,
};

ImprovementRunsView.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  record: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
  backLabel: PropTypes.string,
  /* Runs the chosen winner on the full suite. */
  onVerify: PropTypes.func,
  /* Opens Ship for a verified winner. */
  onShip: PropTypes.func,
  /* Re-run the kept change on today's production version. */
  onRecheck: PropTypes.func,
  /* Running: from improvementProgressOf — the stage and runs finished so far. */
  progress: PropTypes.object,
  onOpenIssue: PropTypes.func,
  /* The run it started from, when it started from one. */
  onOpenStartRun: PropTypes.func,
  /* A tab to open, asked from outside the page (Ship's See changes). */
  tabAsk: PropTypes.string,
  onTabAsked: PropTypes.func,
};
