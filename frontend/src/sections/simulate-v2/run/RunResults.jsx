import PropTypes from "prop-types";
import IssueReturnBanner from "../errorFeed/IssueReturnBanner";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorBoundary } from "react-error-boundary";
import { useSnackbar } from "notistack";
import { useRoleGates } from "../_mock/previewRole";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tab,
  Tooltip,
  Drawer,
  Alert,
  Popover,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Switch,
  useMediaQuery,
} from "@mui/material";
import SideDrawer from "../components/SideDrawer";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { ObjectFilterPanel } from "src/components/filter";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import { subTasksFor } from "../_mock/contract";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { getEval } from "../_mock/evals";
import {
  columnResultsOf,
  denominatorLine,
  evaluationCountsOf,
  evaluationVersionOf,
  evidenceOfSource,
  runEvaluation,
  wrapTrials,
} from "../_mock/evalEvidence";
import { actorsInRun, trialActorOf } from "../_mock/trialFilters";
import { scenarioTitle } from "../_mock/scenarios";
import { SectionCard, StatusChip, EmptyState } from "../components/primitives";
import RunAnalytics from "./RunAnalyticsV2";
import CallDrawer from "./CallDrawer";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";
import { effectiveModality } from "../_mock/rlContract";
import { taskToVoiceData } from "../_mock/voiceCallData";
import { subGoalResultsOf } from "../_mock/scenarioSubGoals";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import EditEvalDrawer from "../workspace/evals/EditEvalDrawer";
import { canEditEval } from "../workspace/evals/canEditEval";
import { useAppliedEvals } from "../workspace/evals/useAppliedEvals";
import { useEnvState } from "../store";
import useRunFitCheck from "./useRunFitCheck";
import {
  runPlanHref,
  attemptHeaderOf,
  attemptsOf,
  retriedIdsOf,
} from "../_mock/runPlanLink";
import { RATE_PLAN } from "../_mock/rates";
import { runSummaries, trialSummaries } from "../_mock/comparison";
import { textTone } from "../components/textTone";
import { resolveEval } from "../_mock/evals";
import TraceTable, {
  TraceGroupByPicker,
  TraceColumnsPicker,
} from "./TraceTable";
import {
  defaultTraceColumns,
  deriveUseCaseLabel,
  ruleOf,
} from "./traceTableHelpers";
import FixMyAgentDrawer from "./fixmyagent/FixMyAgentDrawer";
import {
  improvementOf,
  isCandidateRun,
  runIdentityOf,
} from "../_mock/runIdentity";
import { isConnectOnly, runPinsOf } from "../_mock/envModel";
import { REPLAY_HEADER, stubbedToolNames } from "../_mock/replay";
import FromImprovement from "./FromImprovement";
import { improvementPath } from "../improvements/improvementNav";
import { IssueRunOrigin } from "../errorFeed/FixRunNote";
import RunGroupBar from "./RunGroupBar";
import InsightsTab from "./InsightsTab";
import CostTab from "./CostTab";
import NextStepBanner from "../workspace/NextStepBanner";
import ImprovementSourcesList from "../improvements/ImprovementSourcesList";
import CostStrip from "./CostStrip";
import { TraceSheet } from "./TraceWaterfall";
import { runTracingOf, runIssueImprovement } from "../errorFeed/runIssueFix";
import { startImproverSession, useIntegrations } from "../_mock/integrations";
import { useSimStore } from "../store";
import { envAgentKey } from "../_mock/agentRegistry";
import { membersOf, rereadFor } from "../_mock/improvementPolicy";
import { recordOutsideChange } from "../_mock/nudgeEffects";
import { runReportOf } from "../_mock/runReport";
import RunSummaryChip, { ProofKindChip } from "./RunSummaryChip";
import { runViaOf, ciOf } from "../_mock/runVia";
import { memberResultsOf } from "../_mock/memberAttribution";
import { delegationEvidenceOf } from "../_mock/handoffs";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { causeOf, outcomeOfCause } from "../_mock/decoupled";
import {
  regradeOf,
  regradeRecordOf,
  regradesOf,
  latestRegradeOf,
  resultCountsOf,
  withRegradeRecord,
} from "../_mock/regrade";
import { repeatActionsOf } from "../_mock/repeatActions";
import { RepeatGroup, RepeatRow } from "./RepeatChooser";
import { agentVersions } from "../_mock/versions";
import { runSkillsLineOf } from "../_mock/skillLibrary";
import { scenarioAtVersion } from "../_mock/decoupled";
import MeteredText from "./RunRecord";
import ShareMenu from "./ShareMenu";
import CopyIdButton from "../components/CopyIdButton";
import CopyCommandButton from "../components/CopyCommandButton";
import HandoffTimeline from "./HandoffTimeline";
import SharedState from "./SharedState";
import { trialArtifactsOf } from "../_mock/trialEvidence";
import {
  CheckSource,
  DisputeNotes,
  DuplicateWriteNote,
  TrialArtifacts,
} from "./TrialEvidence";
import ProviderEvents from "./ProviderEvents";
import { COMPAT_STATUS, isTextRehearsal } from "../_mock/compatibility";

import CapabilitySummary from "../compat/CapabilitySummary";
import ApprovalTimeline from "./ApprovalTimeline";
import { approvalOf } from "../_mock/approvals";
import ImprovementRunStrip from "./ImprovementRunStrip";

import { SHOW_INSIGHTS } from "../_mock/featureFlags";
import EvaluateTab from "./evaluate/EvaluateTab";
import RunInsightsTab from "./runInsights/RunInsightsTab";
import { nextSnapshotLabel } from "../_mock/envSnapshot";
import logger from "src/utils/logger";
import { ConfirmDialog } from "src/components/custom-dialog";
import { proofKindOf } from "../_mock/rsiModel";

/**
 * Run results.
 *
 * Ordered by what a user does next: how did it go overall, which tasks failed
 * and why, then verify the builder itself, then optimise. Each of
 * those is a tab rather than a separate page so the run stays one object.
 */
/* Re-score a task's re-run eval columns to fresh, deterministic values (seeded
   by the per-eval version bump). Only evals present in `rescore` change; the
   rest of the task is untouched. */
function rescoreTaskEvals(t, rescore) {
  if (!t.evalResults?.length) return t;
  let changed = false;
  const evalResults = t.evalResults.map((r) => {
    const v = rescore[r.id];
    if (!v) return r;
    changed = true;
    let h = 0;
    const key = `${t.id}:${r.id}:${v}`;
    for (let i = 0; i < key.length; i += 1)
      h = (h * 31 + key.charCodeAt(i)) >>> 0;
    const score = Math.round((0.5 + (h % 50) / 100) * 100) / 100; // 0.50–0.99
    return { ...r, score, passed: score >= (r.threshold ?? 0.8) };
  });
  return changed ? { ...t, evalResults } : t;
}

export default function RunResults({ env, runId, tasks, stats, evals }) {
  const { envId } = useParams();
  const { envState, patch, addAgentVersion } = useEnvState(envId);
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  /* Per-eval-column actions (from the column menu): a set of columns the user
     deleted, and a re-score version bump per column that regenerates just that
     column's cell values (no full simulation). */
  const [deletedEvals, setDeletedEvals] = useState(() => new Set());
  const [evalRescore, setEvalRescore] = useState({}); // evalId -> version
  const [rescoringEval, setRescoringEval] = useState(null); // evalId currently re-scoring

  /*
    Adhoc evals: an evaluation applied to a subset of this run's scenarios
    without re-running the calls. New columns appear in the trace table;
    only the scenarios that were selected at the time carry a score, and
    the rest show "—" with a tooltip explaining why.

    Each one is saved with the run as an evaluation result (its own id, the
    evaluator and version, the evidence it read) beside its Instant eval
    answers, so it is still here after a reload — and read back on the
    Evaluate tab.
  */
  const savedResults = envState.trialEvals?.[runId];
  const adhocEvals = useMemo(
    () =>
      [...columnResultsOf(savedResults)].reverse().map((r) => ({
        id: r.evaluator.id,
        resultId: r.id,
        name: r.evaluator.name,
        version: r.evaluator.version,
        icon: r.evaluator.icon,
        ranAt: r.at,
        /* Every row the result read, graded or not; a grading error has
           no score to put in the table. */
        taskIds: Object.keys(r.results),
        errorIds: Object.keys(r.results).filter((k) => r.results[k].error),
        results: Object.fromEntries(
          Object.entries(r.results).filter(([, v]) => !v.error),
        ),
      })),
    [savedResults],
  );
  /* Which scenarios the user had selected when they picked "Run evals on
     these" — captured then, not read from `selected` later, so opening the
     drawer doesn't strand the answer to "on which scenarios?". */
  const [adhocIds, setAdhocIds] = useState([]);
  const [addingAdhocEvals, setAddingAdhocEvals] = useState(false);
  const [rerunAnchor, setRerunAnchor] = useState(null);

  /*
    A run always measures something — a scenario can't have run without
    at least one grader firing against it. But the `evals` prop reflects
    what the env has applied *now*, which is empty for seeded envs (and
    can drift for older runs after evals were added or removed). The
    columns should show what this run actually measured, so we derive
    them from the union of `task.evalResults` and fall back to the env's
    current list only when the tasks carry nothing.
  */
  const shownEvals = useMemo(() => {
    const byId = new Map();
    tasks.forEach((t) => {
      (t.evalResults || []).forEach((r) => {
        if (!byId.has(r.id)) {
          const known = getEval(r.id);
          byId.set(r.id, {
            id: r.id,
            name: r.name || known?.name || r.id,
            icon: known?.icon || "solar:shield-check-linear",
          });
        }
      });
    });
    const all = byId.size ? [...byId.values()] : evals;
    const known = all.filter((e) => !deletedEvals.has(e.id));
    /* Adhoc columns. Fold them in after the known columns, marked with
       adhoc + the set of task ids that were included, so the cell renderer
       can tell an unscored row apart from a missing result. Multiple adhoc
       runs of the same eval merge their coverage. */
    if (adhocEvals.length) {
      const knownIds = new Set(known.map((e) => e.id));
      const byAdhocId = new Map();
      adhocEvals.forEach((ae) => {
        if (knownIds.has(ae.id) || deletedEvals.has(ae.id)) return;
        const existing = byAdhocId.get(ae.id);
        const includedIds = new Set([
          ...(existing?.includedIds || []),
          ...ae.taskIds,
        ]);
        /* A later result for the same row replaces an earlier one: a
           score, or a grading error, is the row's latest word. */
        const results = new Map(existing?.results || []);
        const errorIds = new Set(existing?.errorIds || []);
        ae.taskIds.forEach((tid) => {
          if (ae.errorIds.includes(tid)) {
            errorIds.add(tid);
            results.delete(tid);
          } else {
            errorIds.delete(tid);
            results.set(tid, ae.results[tid]);
          }
        });
        const kn = getEval(ae.id);
        byAdhocId.set(ae.id, {
          id: ae.id,
          name: ae.name || kn?.name || ae.id,
          icon: ae.icon || kn?.icon || "solar:shield-check-linear",
          adhoc: true,
          adhocRanAt: ae.ranAt,
          resultId: ae.resultId,
          version: ae.version,
          includedIds,
          errorIds,
          results,
        });
      });
      known.push(...byAdhocId.values());
    }
    return known;
  }, [tasks, evals, deletedEvals, adhocEvals]);

  const rerunEvalColumn = (e) => {
    setRescoringEval(e.id);
    setTimeout(() => {
      setEvalRescore((prev) => ({ ...prev, [e.id]: (prev[e.id] || 0) + 1 }));
      setRescoringEval(null);
    }, 900);
  };
  /* Only hides the column in this view — the evaluation, its results and
     the environment are untouched, and it comes back on a reload. Undo puts
     it back now. */
  const hideEvalColumn = (e) => {
    setDeletedEvals((prev) => new Set(prev).add(e.id));
    enqueueSnackbar(`${e.name} column hidden`, {
      variant: "default",
      autoHideDuration: 8000,
      action: (key) => (
        <Button
          variant="outlined"
          size="small"
          color="inherit"
          onClick={() => {
            closeSnackbar(key);
            setDeletedEvals((prev) => {
              const next = new Set(prev);
              next.delete(e.id);
              return next;
            });
          }}
        >
          Undo
        </Button>
      ),
    });
  };
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  /* Opened from an Error Feed issue's fix (View fix, the status chip): back
     goes to that issue, named — on a reload too, while the entry lasts. */
  const location = useLocation();
  /* Kept for the page's life: in-page links (?improve=, tabs) push entries
     without it. */
  const [fromIssue] = useState(() => location.state?.fromIssue || null);
  /*
    A comparison can send scenarios straight here — "these four broke, work out
    why". Landing on the default tab with no memory of what was sent would make
    the reader find them again by hand, so the link carries both the tab and
    the subset.
  */
  /* `optimize` is the old name for this tab; links minted before it became
     it became Optimization runs still carry it. */
  /* The step a diagnosis named, carried into the trace drawer. */
  const [focusStep, setFocusStep] = useState(null);
  /* Fix my agent is a drawer over whatever you were reading, not a tab you
     navigate away to — the diagnosis is about the run still on screen. */
  /* `?improve=OPT-…` lands with the drawer open on that improvement run —
     how the Improvements page links into one. */
  /* `?improveNew=1` (Improve agent on a confirmed regression) opens a new one. */
  const [fixOpen, setFixOpen] = useState(
    () => !!params.get("improve") || !!params.get("improveNew"),
  );
  const [fixOptId, setFixOptId] = useState(() => params.get("improve"));
  /* "From Improvement run N" on this run's header links here with
     `?improve=` — the page is already mounted, so open it on the change too. */
  const improveParam = params.get("improve");
  useEffect(() => {
    if (improveParam) {
      setFixOptId(improveParam);
      setFixOpen(true);
    }
  }, [improveParam]);
  const [tab, setTab] = useState(() => {
    /* Insights leads once the run is finished — this screen only shows
       finished runs; a live one stays on the live view. */
    const t = params.get("tab") || (SHOW_INSIGHTS ? "insights" : "tasks");
    /* Stale deep-links to removed tabs — the old "Self improvement runs"
       (`optimize`/`omega`), the "Verify" tab, and the now-retired "Trials"
       tab (self-improvement trials are peer runs on the Runs list) — all
       land on Test runs. */
    /* The retired "By member" tab is now Group by Member on Scenarios. */
    return t === "optimize" ||
      t === "omega" ||
      t === "verify" ||
      t === "trials" ||
      t === "members"
      ? "tasks"
      : t;
  });
  const [openTask, setOpenTask] = useState(null);
  /* Which left pane the call drawer opens on — "trace" from Insights. */
  const [openPane, setOpenPane] = useState(null);
  /* A voice run's call drawer is Observe's; its trace opens on its own. */
  const [traceTask, setTraceTask] = useState(null);
  /* Eval the drawer opens on when a score cell was clicked — kept across
     prev/next so stepping through calls stays on the same eval. */
  const [evalFocus, setEvalFocus] = useState(null);
  /*
    Metric-driven drill-down filter set by clicking a bucket in the
    RunMetrics drill-down panel. Shape:
    { kind, value, label, match(task): boolean } | null
  */
  const [bucketFilter, setBucketFilter] = useState(null);
  /*
    Grouping mode for the traces table. Lifted here so the picker can
    live in the SectionCard header where the title used to sit.
  */
  const [groupBy, setGroupBy] = useState(() =>
    params.get("tab") === "members" ? "member" : "useCase",
  );
  /* Expand all lives in the toolbar: the table reports whether every group
     is open (so the switch is right after opening groups one by one), and
     the switch asks the table to open or close them all. */
  const [allOpen, setAllOpen] = useState(false);
  const [expandCommand, setExpandCommand] = useState(null);
  /*
    Column visibility for the traces table. A Set of column keys the
    user has chosen to show; essentials (Persona, Scenario) render
    regardless. Lifted so the picker sits in the header action slot.
  */
  const [visibleColumns, setVisibleColumns] = useState(() =>
    defaultTraceColumns(),
  );
  /*
    Quick status filter chips sitting beside the Columns picker. A fast
    one-click narrowing by outcome (All / Failing / Flaky / Inconclusive /
    Passing), separate from the attribute FilterPanel — the two AND together.
  */
  const [statusChip, setStatusChip] = useState("all");
  /* "Failure sub-goal" and "Failure pattern" are failure-only views — passed
     and inconclusive tasks have no failure bucket, so grouping by them drops
     those tasks entirely. Selecting one of those groupings while the status
     chip asks for Passing/Inconclusive is contradictory (it empties the
     table), so the two controls are reconciled: incompatible chips are
     disabled, and switching to a failure grouping resets an incompatible
     chip back to All. */
  const failureGrouping = groupBy === "subGoal" || groupBy === "pattern";
  const incompatibleChips = failureGrouping
    ? ["passing", ...NOT_COUNTED_CHIPS]
    : [];
  const setGroupByReconciled = (g) => {
    setGroupBy(g);
    const failView = g === "subGoal" || g === "pattern";
    if (
      failView &&
      (statusChip === "passing" || NOT_COUNTED_CHIPS.includes(statusChip))
    ) {
      setStatusChip("all");
    }
  };
  /*
    Attribute filters (Goal, Sub-goal, Status, Failure pattern, Critical).
    Uses the platform's shared FilterPanel component so the popover
    (AI / Basic / Query tabs) reads the same as Evals and Datasets.
    Shape is a flat object of arrays — {goal:[…], subGoal:[…], status:[…],
    pattern:[…], critical:[…]} — the same shape the panel's Basic tab
    emits and the same shape the predicate below reads.
  */
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [filters, setFilters] = useState({});
  const filterCount = Object.values(filters).reduce(
    (a, v) => a + (v?.length || 0),
    0,
  );
  const [addingEvals, setAddingEvals] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  /* Calls in a voice environment open the voice drawer, whatever agent is
     under test. */
  const isVoiceRun =
    env?.surface === "voice" || effectiveModality(env, envState) === "voice";
  /* The voice call drawer hides while one opened from it (an eval, a fix) is
     open. Other runs use SideDrawer, which registers itself — registering
     here too put this entry on top and hid that drawer behind itself. */
  const callCovered = useDrawerCovered(!!openTask && isVoiceRun);
  /* Same edit drawer the Evaluations tab uses — the variable mapping and any
     configurable fields live there. Opens when a column is edited from its
     kebab menu. Delegated to the environment's applied evals so a saved edit
     shows up wherever the eval is used. */
  const {
    appliedEvals,
    update: updateAppliedEval,
    add: addAppliedEvals,
    slotsLeft: evalSlotsLeft,
  } = useAppliedEvals(envState, patch);
  const [editingEvalId, setEditingEvalId] = useState(null);
  const editingEval = editingEvalId
    ? appliedEvals.find((x) => x.id === editingEvalId)
    : null;
  const editEvalColumn = (e) => {
    /* The header hands us the eval as it sits in the run's results — id,
       name, icon. Look up the applied eval on the environment to get the
       template / custom flags canEditEval reads. */
    const applied = appliedEvals.find((x) => x.id === e.id);
    if (!applied || !canEditEval(applied)) return;
    setEditingEvalId(e.id);
  };

  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(
    env,
    envState,
    patch,
  );
  const { state: simState, commit: simCommit } = useSimStore();
  const integrations = useIntegrations();
  /* Fix on an issue needs trace evidence — hosted agents report every call,
     code agents need traceAI. */
  const tracing = useMemo(
    () => runTracingOf(envState?.agent, integrations, env?.id),
    [envState?.agent, integrations, env?.id],
  );

  /*
    Which run this is. The header said "Run complete" and then printed the raw
    id, so a screen reached from a list of five runs never told you which of
    the five you were looking at — and the id is the one label nobody in the
    list was reading.
  */
  const summaries = runSummaries(env, envState);
  const identity = summaries.find((r) => r.id === runId);
  /* Improvements started from this run live on its own tab, not on the
     environment's run list. */
  const improvedFrom = (envState.optimizations || []).filter(
    (o) => o.fromRunId === runId,
  ).length;

  /*
    Trial detection. A trial id is `${OPT-XXXXX}-t${n}`. When someone lands
    here from the runs list, name the parent search — so the reader always
    knows which self improvement's trial they are inside of, and can open
    the parent to see how this trial ranked against its siblings.
  */
  const trialMeta = useMemo(() => {
    const m = /^(OPT-\d+)-t(\d+)$/.exec(runId || "");
    if (!m) return null;
    const opt = (envState.optimizations || []).find((o) => o.id === m[1]);
    if (!opt) return null;
    const trialN = Number(m[2]);
    const trial = (opt.result?.trials || []).find((t) => t.n === trialN);
    if (!trial) return null;
    const ordinal = (envState.optimizations || []).indexOf(opt) + 1;
    return {
      opt,
      trial,
      ordinal,
      isWinner: opt.result?.winner?.n === trialN,
      winnerTrialN: opt.result?.winner?.n,
    };
  }, [runId, envState.optimizations]);

  /* "agent v3 × env v2", and the improvement that produced it — named the way
     its row on the runs list names it. A trial is read off the same trial
     summary that row is. */
  const named = useMemo(() => {
    const run = trialMeta
      ? trialSummaries(env, envState).find((t) => t.id === runId)
      : identity;
    return run ? runIdentityOf(run, envState, env) : null;
  }, [trialMeta, identity, env, envState, runId]);

  /* Made by an improvement to try a change. Its failures are the
     improvement's to read, so Debug failures isn't offered here. */
  /* A fix's first run is the fix's starting point, not one of its
     candidates — the fix is already working on these failures. */
  const fixFirst =
    (envState.runs || []).find((r) => r.id === runId)?.measuredFor?.kind ===
    "fix";
  /* The issue's scenarios ride along uncounted — their result is what the
     fix came for, so the header says it beside the run's own status. */
  const fixIssueId =
    (envState.runs || []).find((r) => r.id === runId)?.measuredFor?.issueId ||
    "this issue";
  const fixOfRun = fixFirst
    ? (envState.optimizations || []).find((o) => o.fromRunId === runId)
    : null;
  /*
    The issue's scenarios ride along uncounted — their result is what the fix
    came for. Read as the fix reads it (its own reproduce result): a scenario
    reproduces only when it failed every repeat; failing once is
    inconclusive — so this page and the fix never disagree.
  */
  const issueResult = useMemo(() => {
    if (!fixFirst) return null;
    const own = (tasks || []).filter((t) => t.fixScenario);
    const rep = fixOfRun?.reproduce;
    if (rep?.total)
      return {
        total: rep.total,
        reproduced: rep.reproduced || 0,
        inconclusive: rep.inconclusive || 0,
        unscored: own.filter((t) => t.status === "unmeasured").length,
      };
    if (!own.length) return null;
    return {
      total: own.length,
      reproduced: own.filter((t) => t.status === "failed").length,
      inconclusive: own.filter((t) => t.status === "flaky").length,
      unscored: own.filter((t) => t.status === "unmeasured").length,
    };
  }, [fixFirst, fixOfRun, tasks]);
  const candidate =
    !fixFirst &&
    (!!trialMeta ||
      !!named?.improvement ||
      isCandidateRun({ ...(identity || {}), id: runId }, envState));

  /*
    The run as one record — its release verdict and execution health, what it
    metered, and the digest it sent — read the same way the shared report and
    the completion digest read it. Keyed on the inputs, not the summaries
    (rebuilt every render).
  */
  const report = useMemo(
    () =>
      runReportOf({
        env,
        envState,
        runId,
        tasks,
        summaries,
        goalOf: deriveUseCaseLabel,
        integrations,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState, runId, tasks, integrations],
  );
  /* An improvement's candidate runs don't digest — a search runs dozens. */
  const record = {
    metering: report.metering,
    digest: candidate ? null : report.digest,
    destinations: identity?.digest || report.destinations,
    sentAt: identity?.finishedAt,
  };
  /* Tools the agent was told to stub under the replay flag. */
  const stubbed = stubbedToolNames(env, envState);

  /*
    A result you disagree with is usually a result that was graded on the wrong
    thing. Adding a grader here and running again is the shortest path from
    "that score looks wrong" to a run that measures what you actually meant —
    previously it meant walking back to the Evals step to find out.
  */
  /*
    Re-running with rows ticked used to start a full sweep, which is not what
    the button said and buries the four scenarios someone was actually chasing.
    The subset travels with the run and the run records that it was a subset.
  */
  /*
    A re-run repeats the test it came from — same number of trials — in one of
    two modes (PRD v7 §4.10.8):
      snapshot — "Re-run exact snapshot": the source run's scenario versions,
                 environment version, pins and seed; only the agent version
                 may change. The default for comparisons.
      now      — "Run as configured now": picks up every edit since.
  */
  const sourceRun = (envState.runs || []).find((r) => r.id === runId) || null;
  /* Retries are linked attempts of this run: what they took over isn't offered again. */
  const retriedIds = retriedIdsOf(sourceRun);
  const retryLeft = (sourceRun?.budget?.notStarted || []).filter(
    (id) => !retriedIds.includes(id),
  );
  const lastAttempt = attemptsOf(sourceRun).slice(-1)[0] || null;
  /* The capability report as it stood when this run started — kept with it,
     so a later check doesn't rewrite what this run ran under. */
  const runCaps = sourceRun?.compat?.rows?.length ? sourceRun.compat : null;
  /* The worst capability colours the run's Capabilities chip. */
  const capsWorst = runCaps
    ? runCaps.rows.reduce(
        (w, r) =>
          COMPAT_STATUS[r.status].rank > COMPAT_STATUS[w].rank ? r.status : w,
        "supported",
      )
    : null;
  /* The report opens over the page, so the header never grows. */
  const [capsOpen, setCapsOpen] = useState(false);
  const capsRef = useRef(null);
  const capsLine = runCaps
    ? Object.keys(COMPAT_STATUS)
        .filter((k) => k !== "supported")
        .sort((a, b) => COMPAT_STATUS[b].rank - COMPAT_STATUS[a].rank)
        .map((k) => [k, runCaps.rows.filter((r) => r.status === k).length])
        .filter(([, n]) => n)
        .map(([k, n]) => `${n} ${COMPAT_STATUS[k].label.toLowerCase()}`)
        .join(" · ") || "all supported"
    : null;
  const canSnapshot = !!sourceRun?.finishedAt && !trialMeta;
  /* A snapshot re-run plays its source's pinned versions — say how many of
     those are older than today's text, so a diff against a fresh run isn't
     read as the agent's doing. Only versions whose text was kept count. */
  const snapshotNote = (() => {
    if (!sourceRun?.snapshotOf || !sourceRun.scenarioVersions) return null;
    const ids = sourceRun.scenarioIds?.length
      ? new Set(sourceRun.scenarioIds)
      : null;
    const older = (envState.scenarios || []).filter(
      (r) =>
        (!ids || ids.has(r.id)) &&
        sourceRun.scenarioVersions[r.id] &&
        scenarioAtVersion(r, sourceRun.scenarioVersions[r.id]) !== r,
    );
    if (!older.length) return null;
    const ordinal =
      (envState.runs || []).find((r) => r.id === sourceRun.snapshotOf)
        ?.ordinal ?? "—";
    return `${older.length} scenario${older.length === 1 ? "" : "s"} played ${older.length === 1 ? "its" : "their"} text as it was in run ${ordinal}`;
  })();
  const gates = useRoleGates();
  const rerun = (
    ids,
    { mode = "now", agent = null, toolReplay = null } = {},
  ) =>
    !gates.canRun
      ? enqueueSnackbar(gates.runReason, { variant: "info" })
      : checkRunFit(() => {
          /* Through the run plan, like every run: scope, estimate and limit. */
          const snap = mode === "snapshot" && canSnapshot;
          navigate(
            runPlanHref(envId, {
              only: ids || [],
              trials: sourceRun?.repeats || 3,
              agent: snap
                ? agent || sourceRun.agentVersion
                : agent || undefined,
              snapshot: snap ? sourceRun.id : undefined,
              envVersion: snap ? sourceRun.envVersion : undefined,
              toolReplay: toolReplay || undefined,
            }),
          );
        });
  const [runRerunAnchor, setRunRerunAnchor] = useState(null);
  /* Retry interrupted: confirmed first — it lists what restarts. */
  const [retryConfirm, setRetryConfirm] = useState(false);
  /* The scenarios that didn't pass — what "Re-run N failing" replays. */
  /* A fix's first run debugs only what isn't the issue's — the fix already
     has the issue. */
  const debugTasks = useMemo(() => {
    if (!fixFirst) return tasks;
    /* The environment's scenarios the fix took in (ones that fail the same
       way, held back to prove it) are the fix's too. */
    const ids = (x) =>
      (x || []).map((t) => (typeof t === "string" ? t : t?.id));
    const inFix = new Set([
      ...ids(fixOfRun?.result?.train),
      ...ids(fixOfRun?.result?.held),
    ]);
    return tasks.filter((t) => !t.fixScenario && !inFix.has(t.id));
  }, [fixFirst, fixOfRun, tasks]);
  const failingScenarioIds = useMemo(
    () => [
      ...new Set(
        debugTasks
          .filter((t) => t.status === "failed" || t.status === "flaky")
          .map((t) => t.scenarioId || t.id),
      ),
    ],
    [debugTasks],
  );
  /* Failure analysis is priced and charged once per run: the first open
     records the charge on the run; opening it again reads the same one. */
  const analysisCharge =
    (envState.runs || []).find((r) => r.id === runId)?.analysis || null;
  const [debugConfirm, setDebugConfirm] = useState(null);
  const analysisCredits =
    failingScenarioIds.length * RATE_PLAN.rates.analysis_calls.credits;
  /* Debug selected: only the failures picked in the table go to the
     diagnosis and the improvement — the rest of the run's failures are left
     out, its passing scenarios stay as what must keep passing. */
  const [debugIds, setDebugIds] = useState(null);
  /* Improve agent from a finding on Insights: the same drawer, on the
     finding's scenarios, straight to the improvement's setup — the step
     Debug failures → Improve reaches. Nothing is read first, so nothing
     is charged here. */
  const [fixSetup, setFixSetup] = useState(false);
  const openImprove = (ids) => {
    setDebugIds(ids);
    setFixOptId(null);
    setFixSetup(true);
    setFixOpen(true);
  };
  const isFailingTask = (t) => t.status === "failed" || t.status === "flaky";
  const selectedFailingIds = [
    ...new Set(
      debugTasks
        .filter(
          (t) =>
            isFailingTask(t) &&
            (selected.has(t.id) || selected.has(t.scenarioId)),
        )
        .map((t) => t.scenarioId || t.id),
    ),
  ];
  const drawerTasks = debugIds
    ? debugTasks.filter(
        (t) => !isFailingTask(t) || debugIds.includes(t.scenarioId || t.id),
      )
    : debugTasks;
  /* Charged per scenario read, once: a later read of more of the run's
     failures pays only for the ones not read yet. */
  const openDebug = (ids = null) => {
    const want = ids || failingScenarioIds;
    const read = analysisCharge ? analysisCharge.ids || failingScenarioIds : [];
    const fresh = want.filter((id) => !read.includes(id));
    if (fresh.length) {
      patch({
        runs: (envState.runs || []).map((r) =>
          r.id === runId
            ? {
                ...r,
                analysis: {
                  credits:
                    (analysisCharge?.credits || 0) +
                    fresh.length * RATE_PLAN.rates.analysis_calls.credits,
                  scenarios: read.length + fresh.length,
                  ids: [...read, ...fresh],
                  at: analysisCharge?.at || new Date().toISOString(),
                },
              }
            : r,
        ),
      });
    }
    setDebugIds(ids);
    setFixOptId(null);
    setFixOpen(true);
  };

  /* Re-grade (AC-12.23): offered when a sub-goal this run used got a grader
     fix, or the eval version moved, since it ran. Priced; starts on a click. */
  const regrade = useMemo(
    () =>
      trialMeta
        ? null
        : regradeOf({
            env,
            envState,
            run: sourceRun,
            evals: (sourceRun?.evals || envState.evals || [])
              .map(resolveEval)
              .filter(Boolean),
          }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState, sourceRun, trialMeta],
  );
  const startRegrade = () => {
    if (!regrade || !sourceRun) return;
    /* The result being replaced is kept with the re-grade, never overwritten. */
    const rec = regradeRecordOf(regrade, { before: resultCountsOf(tasks) });
    patch({
      runs: (envState.runs || []).map((r) =>
        r.id === sourceRun.id ? withRegradeRecord(r, rec) : r,
      ),
    });
  };
  /* Re-evaluating keeps the earlier result: the history lists what each re-grade replaced. */
  const regradeHistory = regradesOf(sourceRun);
  const latestRegrade = latestRegradeOf(sourceRun);
  const [showEarlier, setShowEarlier] = useState(false);
  const regradedChanged = tasks.filter((t) => t.regraded?.changed).length;
  /* Every way to repeat this run, for the one Repeat chooser. */
  const repeat = repeatActionsOf({
    env,
    envState,
    run: sourceRun,
    tasks,
    regrade,
  });

  /* The user's own bar for pass^1 — it colours reports, nothing more. */
  const setReportBar = (pass1) =>
    patch({ reportBar: { ...(envState.reportBar || {}), pass1 } });

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  /*
    Options for each filterable field, derived from the tasks in this
    run so the popover only offers choices that will actually match.
    Sub-goal options are unioned across every task's `subTasksFor`
    breakdown so a filter like "touches Verify who is asking" works
    across scenarios.
  */
  const goalOptions = useMemo(() => {
    const s = new Set();
    tasks.forEach((t) => {
      const g = deriveUseCaseLabel(t);
      if (g) s.add(g);
    });
    return [...s].sort();
  }, [tasks]);
  const subGoalOptions = useMemo(() => {
    const s = new Set();
    tasks.forEach((t) => subTasksFor(t, env).forEach((sg) => s.add(sg.label)));
    return [...s].sort();
  }, [tasks, env]);
  const STATUS_LABELS = [
    "Passed",
    "Failed",
    "Unmeasured",
    "Test invalid",
    "Error",
  ];
  /* Verdict · cause (§4.10.10), from the run summary. */
  const verdicts = report.summary?.verdicts;
  const causeOfTask = (t) => {
    const v = verdicts?.get(t.id);
    return v ? v.label : null;
  };
  /* Squads, conversation flows and handoff apps: Group by Member, and the
     one routing fact grouping can't show — how often the entry member
     handed the conversation on. */
  const memberResult = memberResultsOf({
    agent: envState?.agent,
    tasks,
    causeOf: causeOfTask,
  });
  const memberList = useMemo(
    () => membersOf(envState?.agent),
    [envState?.agent],
  );
  const entryMember = memberResult?.rows.find((r) => r.entry);
  const CAUSE_LABELS = [
    ...new Set(tasks.map(causeOfTask).filter(Boolean)),
  ].sort();
  const PATTERN_LABELS = [
    "Critical rule broken",
    "Said, not done",
    "Evaluation failed",
    "Error",
    "Other failure",
  ];
  const CRITICAL_LABELS = ["Critical", "Not critical"];
  /* Rule, Scenario and Actor — the same readings as the Rule broken and
     Task groupings, and the live run's actor filter. */
  const ruleOptions = useMemo(
    () => [...new Set(tasks.map(ruleOf).filter(Boolean))].sort(),
    [tasks],
  );
  const scenarioOptions = useMemo(
    () =>
      [
        ...new Set(
          tasks.map((t) => scenarioTitle(t.title || t.name)).filter(Boolean),
        ),
      ].sort(),
    [tasks],
  );
  const actorOptions = useMemo(() => actorsInRun(tasks), [tasks]);

  const filterFields = useMemo(
    () => [
      { value: "goal", label: "Goal", type: "enum", choices: goalOptions },
      {
        value: "subGoal",
        label: "Sub-goal",
        type: "enum",
        choices: subGoalOptions,
      },
      {
        value: "status",
        label: "Status",
        type: "enum",
        choices: STATUS_LABELS,
      },
      {
        value: "cause",
        label: "Verdict cause",
        type: "enum",
        choices: CAUSE_LABELS,
      },
      {
        value: "pattern",
        label: "Failure pattern",
        type: "enum",
        choices: PATTERN_LABELS,
      },
      {
        value: "critical",
        label: "Criticality",
        type: "enum",
        choices: CRITICAL_LABELS,
      },
      ...(ruleOptions.length
        ? [
            {
              value: "rule",
              label: "Rule",
              type: "enum",
              choices: ruleOptions,
            },
          ]
        : []),
      {
        value: "scenario",
        label: "Scenario",
        type: "enum",
        choices: scenarioOptions,
      },
      ...(actorOptions.length
        ? [
            {
              value: "actor",
              label: "Actor",
              type: "enum",
              choices: actorOptions,
            },
          ]
        : []),
    ],
    /* eslint-disable react-hooks/exhaustive-deps */
    [
      goalOptions,
      subGoalOptions,
      ruleOptions,
      scenarioOptions,
      actorOptions,
      CAUSE_LABELS.join("|"),
    ],
    /* eslint-enable react-hooks/exhaustive-deps */
  );

  /* Per-task derivations, cached so the filter predicate doesn't
     recompute them when only the selected filter changes. */
  const taskDerivations = useMemo(() => {
    const out = new Map();
    tasks.forEach((t) => {
      const statusLabel =
        t.status === "passed"
          ? "Passed"
          : t.status === "unmeasured"
            ? STATUS_CHIPS.find((c) => c.id === statusBucket(t))?.label ||
              "Unmeasured"
            : t.status === "error"
              ? "Error"
              : "Failed";
      let patternLabel = null;
      if (t.status !== "passed" && t.status !== "unmeasured") {
        if (t.status === "error") patternLabel = "Error";
        else if (t.critical) patternLabel = "Critical rule broken";
        else if (t.callLog?.unsupportedClaim) patternLabel = "Said, not done";
        else if ((t.evalResults || []).some((r) => !r.passed))
          patternLabel = "Evaluation failed";
        else patternLabel = "Other failure";
      }
      out.set(t.id, {
        goal: deriveUseCaseLabel(t),
        subGoalLabels: new Set(subTasksFor(t, env).map((s) => s.label)),
        statusLabel,
        causeLabel: causeOfTask(t),
        patternLabel,
        criticalLabel: t.critical ? "Critical" : "Not critical",
        ruleLabel: ruleOf(t),
        scenarioLabel: scenarioTitle(t.title || t.name),
        actorLabel: trialActorOf(t),
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, env, verdicts]);

  const shown = tasks.filter((t) => {
    /* Metric drill-down predicate — clicking a bucket on the RunMetrics
       chart. Independent of the header filters; they AND together. */
    if (bucketFilter?.match && !bucketFilter.match(t)) return false;
    /* Pulled scenarios sit in their own group above the table — unless a
       claim on Insights asked for them by name. */
    if (t.pulled && !bucketFilter) return false;

    if (statusChip !== "all" && statusBucket(t) !== statusChip) return false;

    const d = taskDerivations.get(t.id);
    if (filters.goal?.length && !filters.goal.includes(d.goal)) return false;
    if (filters.status?.length && !filters.status.includes(d.statusLabel))
      return false;
    if (filters.pattern?.length && !filters.pattern.includes(d.patternLabel))
      return false;
    if (filters.critical?.length && !filters.critical.includes(d.criticalLabel))
      return false;
    if (filters.cause?.length && !filters.cause.includes(d.causeLabel))
      return false;
    if (filters.rule?.length && !filters.rule.includes(d.ruleLabel))
      return false;
    if (filters.scenario?.length && !filters.scenario.includes(d.scenarioLabel))
      return false;
    if (filters.actor?.length && !filters.actor.includes(d.actorLabel))
      return false;
    if (
      filters.subGoal?.length &&
      !filters.subGoal.some((g) => d.subGoalLabels.has(g))
    )
      return false;
    return true;
  });

  /*
    The results filters, as a named snapshot — "Failing · Goal: refund" —
    for the Evaluate tab's "Current filter". Taken when an evaluation is
    started, so it keeps naming the trials it read after the filters change.
  */
  const currentFilter = useMemo(() => {
    const parts = [];
    if (statusChip !== "all")
      parts.push(STATUS_CHIPS.find((c) => c.id === statusChip)?.label);
    Object.entries(filters).forEach(([key, values]) => {
      if (!values?.length) return;
      const label = filterFields.find((f) => f.value === key)?.label || key;
      parts.push(`${label}: ${values.join(", ")}`);
    });
    if (bucketFilter?.label) parts.push(bucketFilter.label);
    const named = parts.filter(Boolean);
    return named.length
      ? { name: named.join(" · "), taskIds: shown.map((t) => t.id) }
      : null;
  }, [statusChip, filters, bucketFilter, filterFields, shown]);
  /* Other finished runs of this environment — what a result can also read. */
  const otherRuns = useMemo(
    () =>
      summaries
        .filter((r) => r.id !== runId && r.finishedAt && r.tasks?.length)
        .map((r) => ({
          id: r.id,
          label: `Run ${r.ordinal}`,
          tasks: r.tasks,
          meta: `${new Date(r.finishedAt).toLocaleDateString(undefined, {
            day: "numeric",
            month: "short",
          })} · ${r.tasks.length} trials`,
        })),
    // Runs are rebuilt from their ids; the ids say when they change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [summaries.map((r) => r.id).join("|"), runId],
  );
  const selectedIds = useMemo(() => [...selected], [selected]);

  /* Table view of the shown rows with any re-run eval columns re-scored to
     fresh values. Only the re-run column's cells change; everything else is
     the original run. */
  const tableShown = useMemo(() => {
    let out = Object.keys(evalRescore).length
      ? shown.map((t) => rescoreTaskEvals(t, evalRescore))
      : shown;
    /* Fold adhoc results into each task's evalResults so aggregates
       (group headers, filters, chart) treat them just like any other
       eval — only the cells that lack a result show the "not part of
       this eval run" tooltip. */
    if (adhocEvals.length) {
      const byTask = new Map(); // taskId -> extra evalResults[]
      /* The latest word on each (evaluation, row): oldest first, so a later
         result — a score or a grading error — replaces an earlier one. */
      const latest = new Map();
      adhocEvals.forEach((ae) =>
        ae.taskIds.forEach((tid) =>
          latest.set(`${ae.id}|${tid}`, { ae, tid, cell: ae.results?.[tid] }),
        ),
      );
      latest.forEach(({ ae, tid, cell }) => {
        if (!cell) return;
        const kn = getEval(ae.id);
        const arr = byTask.get(tid) || [];
        arr.push({
          id: ae.id,
          name: ae.name || kn?.name || ae.id,
          color: kn?.color,
          score: cell.score,
          passed: cell.passed,
          reason: cell.reason,
          threshold: cell.threshold,
        });
        byTask.set(tid, arr);
      });
      if (byTask.size) {
        out = out.map((t) => {
          const extras = byTask.get(t.id);
          if (!extras) return t;
          /* Replace an existing adhoc result for the same eval id rather
             than double-counting when the user re-runs the same eval on
             the same row. */
          const seen = new Set(extras.map((r) => r.id));
          const base = (t.evalResults || []).filter((r) => !seen.has(r.id));
          return { ...t, evalResults: [...base, ...extras] };
        });
      }
    }
    return out;
  }, [shown, evalRescore, adhocEvals]);

  /* What will actually render as groups. Failure groupings (sub-goal / pattern)
     have no bucket for passed/unmeasured tasks, so they drop out — used to
     decide between the table and an explanatory empty state. */
  const groupableShown = failureGrouping
    ? shown.filter((t) => t.status !== "passed" && t.status !== "unmeasured")
    : shown;

  /* Counts for the status chips — from the full run so the numbers read as a
     fixed reference the chip filter narrows against, not a moving total. */
  /* Counted within a link's filter (a sub-goal, an issue), so the chips
     describe the rows on screen — not the whole run behind them. */
  const statusCounts = tasks.reduce(
    (c, t) => {
      /* Pulled scenarios are left out of every count. */
      if (t.pulled) return c;
      if (bucketFilter?.match && !bucketFilter.match(t)) return c;
      c.all += 1;
      c[statusBucket(t)] += 1;
      return c;
    },
    {
      all: 0,
      failing: 0,
      mixed: 0,
      inconclusive: 0,
      invalid: 0,
      error: 0,
      passing: 0,
    },
  );

  const applyFilters = (result) => {
    if (!result) {
      setFilters({});
      return;
    }
    if (Array.isArray(result)) {
      /* Query tab — collapse [{field,operator,value}] into {field:[values]}. */
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value)
          ? r.value
          : r.value != null
            ? [r.value]
            : [];
        if (!values.length) return;
        flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      /* Basic tab — already {field:[values]}. */
      setFilters(result);
    }
  };

  /* When this run finished, as a relative label ("finished 2m ago"). Falls
     back across timestamp fields and yields null on a missing/bad date so the
     header shows nothing rather than "Invalid Date". */
  const finishedLabel = runFinishedLabel(identity);
  /* Narrow screens: secondary header actions keep only their icons. */
  const wideHeader = useMediaQuery(useTheme().breakpoints.up("lg"));

  /* ── Insights → the rest of the page ── */
  /* A claim on Insights filters the Scenarios tab to the scenarios behind it. */
  const pulledIds = tasks.filter((t) => t.pulled).map((t) => t.id);
  const filterTo = (label, ids) => {
    const idSet = new Set(ids || []);
    setBucketFilter({ label, match: (t) => idSet.has(t.id) });
    setStatusChip("all");
    setTab("tasks");
  };
  /* Open a scenario's trace, pinned to the span where it went wrong. */
  const openTrace = (t) => {
    if (!t) return;
    if (isVoiceRun) {
      setTraceTask(t);
      return;
    }
    setFocusStep(null);
    setEvalFocus(null);
    setOpenPane("trace");
    setOpenTask(t);
  };
  /* ?task=<scenario> (chat's "show me the trace") opens that trace once. */
  const taskParam = params.get("task");
  const openedFromLink = useRef(null);
  useEffect(() => {
    if (!taskParam || openedFromLink.current === taskParam) return;
    const t = tasks.find(
      (x) => x.id === taskParam || x.scenarioId === taskParam,
    );
    if (!t) return;
    openedFromLink.current = taskParam;
    openTrace(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskParam, tasks]);
  /* Fixes already started from this run's issues, by issue. */
  const existingFixes = useMemo(
    () =>
      Object.fromEntries(
        (envState?.optimizations || [])
          .filter(
            (o) =>
              o.trigger?.source === "run-insights" && o.fromRunId === runId,
          )
          .map((o) => [o.trigger.issueId, o]),
      ),
    [envState?.optimizations, runId],
  );
  /* Fix: the same improvement flow, focused on the issue — opened here. */
  const fixIssue = (issue, cloudAgentId) => {
    /* Re-read the agent first, as the setup dialog does: a change made
       outside Future AGI is saved as a version and becomes the baseline. */
    if (envState?.agent) {
      const reread = rereadFor(
        {
          ...envState.agent,
          key: envState.agent.key || envAgentKey(envState.agent, env.id),
        },
        simState,
      );
      recordOutsideChange(simCommit, simState, {
        agent: {
          ...envState.agent,
          key: envState.agent.key || envAgentKey(envState.agent, env.id),
        },
        reread,
        activateEnvId: env.id,
      });
    }
    const record = runIssueImprovement({
      env,
      es: envState,
      state: simState,
      run: identity || { id: runId },
      tasks,
      issue,
    });
    patch({ optimizations: [...(envState?.optimizations || []), record] });
    if (cloudAgentId)
      startImproverSession({
        agentId: cloudAgentId,
        envId: env.id,
        optId: record.id,
        where: "future-agi",
      });
    setFixOptId(record.id);
    setFixOpen(true);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      {runFitDialog}
      {/* Its environment was built for an Error Feed issue's Fix: the way back. */}
      <IssueReturnBanner envId={env?.id} />
      {/* ── header ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 3,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        {fromIssue ? (
          <Button
            variant="outlined"
            size="small"
            onClick={() =>
              navigate(paths.dashboard.errorFeed.detail(fromIssue))
            }
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={16} />
            }
            sx={{
              fontFamily: "ui-monospace, Menlo, monospace",
              flexShrink: 0,
              minWidth: 0,
            }}
          >
            {fromIssue}
          </Button>
        ) : (
          <IconButton
            size="small"
            onClick={() => {
              /* Back to the page that opened it when that page said so (an
                 improvement's trials); otherwise the environment's Runs tab.
                 Not history: after visiting an improvement from this run's
                 tab, history's previous page is that improvement. */
              navigate(
                location.state?.back?.to ||
                  paths.dashboard.simulate.environmentStep(envId, "runs"),
              );
            }}
          >
            <Iconify
              icon="solar:alt-arrow-left-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        )}
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            {trialMeta ? (
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: "6px",
                  flexShrink: 0,
                  display: "grid",
                  placeItems: "center",
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.secondary",
                  bgcolor: "background.neutral",
                }}
              >
                <Iconify icon="solar:magic-stick-3-bold" width={14} />
              </Box>
            ) : (
              identity && (
                <Box
                  sx={{
                    width: 24,
                    height: 24,
                    borderRadius: "6px",
                    flexShrink: 0,
                    display: "grid",
                    placeItems: "center",
                    typography: "s3",
                    fontWeight: 600,
                    color: textTone(identity.color),
                    bgcolor: (t) =>
                      alpha(
                        identity.color,
                        t.palette.mode === "dark" ? 0.22 : 0.14,
                      ),
                  }}
                >
                  {identity.letter}
                </Box>
              )
            )}
            <Tooltip arrow title={named?.label || ""}>
              <Typography
                noWrap
                sx={{
                  fontSize: 16,
                  lineHeight: "24px",
                  fontWeight: 600,
                  flexShrink: 0,
                  cursor: "default",
                }}
              >
                {/* An improvement's run says whose it is — the environment has
                    its own Run 1 too. */}
                {trialMeta
                  ? `${trialMeta.opt?.name || "Improvement"} · Candidate ${trialMeta.trial.n}`
                  : identity
                    ? `Run ${identity.ordinal}`
                    : "Run complete"}
              </Typography>
            </Tooltip>
            {/*
              Failed means the run failed, not that something in it did. All
              seven scenarios failing is a failed run; three of seven failing
              is a completed run with findings, and calling that "Failed"
              buries the three that passed.
            */}
            <StatusChip
              status={
                identity?.budget?.stoppedBy
                  ? identity.budget.stoppedBy === "stopped"
                    ? "stopped_early"
                    : "stopped_limit"
                  : stats.passed === 0
                    ? "failed"
                    : stats.failed === 0
                      ? "passed"
                      : "completed"
              }
            />
            {issueResult && (
              <Tooltip
                arrow
                title="The issue's scenarios ran with this run but don't count toward its pass rate. Reproduced: still failing."
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    flexShrink: 0,
                    cursor: "default",
                  }}
                >
                  {`The issue's ${issueResult.total}: ${issueResult.reproduced} reproduced${issueResult.inconclusive ? ` · ${issueResult.inconclusive} inconclusive` : ""}${issueResult.unscored ? ` · ${issueResult.unscored} not scored` : ""}`}
                </Typography>
              </Tooltip>
            )}
            {/* Two indicators, apart: how the agent did (a report — pass^1,
                pass^k — never a gate), and did our harness do its job. */}
            <RunSummaryChip summary={report.summary} />
            {/* Sealed · Live · Prompt only, as a mark — its words on hover. */}
            <ProofKindChip proof={report.summary?.proofKind} compact />
            {/* The capability state the run ran under (TH-8877), as one chip —
                its counts on hover, the report on click. */}
            {sourceRun?.finishedAt && (
              <Tooltip
                arrow
                title={
                  runCaps
                    ? `${capsLine} — click for the report as it was when the run started`
                    : "No capability report was taken for this run"
                }
              >
                <Box
                  ref={capsRef}
                  {...(runCaps &&
                    clickable(() => setCapsOpen((v) => !v), {
                      expanded: capsOpen,
                    }))}
                  sx={{
                    display: "flex",
                    flexShrink: 0,
                    ...(runCaps && { ...focusRing, cursor: "pointer" }),
                  }}
                >
                  <StatusBadge
                    size="sm"
                    status={COMPAT_STATUS[capsWorst || "untested"].dot}
                    variant={runCaps ? "badge" : "dot"}
                    label="Capabilities"
                  />
                </Box>
              </Tooltip>
            )}
            {/* A CI run's check result sits with the run's own status; where
                it came from is on the line below. */}
            {sourceRun &&
              ciOf(sourceRun) &&
              (() => {
                const ci = ciOf(sourceRun);
                /* The same pass rate the summary chip shows — conversations
                   passed of those scored — so the two never disagree. */
                const pct = report.summary?.pass1Pct;
                const rate =
                  pct != null
                    ? pct / 100
                    : stats.total
                      ? stats.passed / stats.total
                      : 0;
                const cleared = rate >= ci.failUnder;
                return (
                  <Tooltip
                    arrow
                    title={`Needed ${Math.round(ci.failUnder * 100)}% to pass (--fail-under ${ci.failUnder}); this run passed ${Math.round(rate * 100)}%.`}
                  >
                    <Box sx={{ display: "flex", flexShrink: 0 }}>
                      <StatusBadge
                        status={cleared ? "passed" : "failed"}
                        role={cleared ? "success" : "danger"}
                        label={cleared ? "Check passed" : "Check failed"}
                      />
                    </Box>
                  </Tooltip>
                );
              })()}
            {sourceRun?.snapshotOf && (
              <Tooltip
                arrow
                title="Re-ran the exact snapshot of an earlier run: the same scenario versions, environment version, pins and seed."
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    cursor: "default",
                  }}
                >
                  {`Snapshot of run ${(envState.runs || []).find((r) => r.id === sourceRun.snapshotOf)?.ordinal ?? "—"}`}
                </Typography>
              </Tooltip>
            )}
            {/* A candidate taken from a code snapshot — a commit or an uploaded archive. */}
            {sourceRun?.candidateSource?.label && (
              <Tooltip arrow title={sourceRun.candidateSource.detail || ""}>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    cursor: "default",
                  }}
                >
                  {sourceRun.candidateSource.label}
                </Typography>
              </Tooltip>
            )}
            {snapshotNote && (
              <Tooltip
                arrow
                title="These scenarios were edited after the source run. The snapshot replays the pinned versions; Run as configured now plays today's text."
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    cursor: "default",
                  }}
                >
                  {`· ${snapshotNote}`}
                </Typography>
              </Tooltip>
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.tertiary" }}>
            {/* A run is one agent version against one environment version,
                each scenario played `repeats` times — the context every
                result on this page compares within. */}
            {env.name}
            {identity?.agentVersion ? ` · agent ${identity.agentVersion}` : ""}
            {(() => {
              const line = runSkillsLineOf(
                simState?.workspaceSettings,
                envState.agent,
                sourceRun,
              );
              return line ? ` · ${line}` : "";
            })()}
            {named?.improvement && (
              <>
                {" · "}
                <FromImprovement improvement={named.improvement} />
              </>
            )}
            <IssueRunOrigin
              envId={env.id}
              run={(envState.runs || []).find((r) => r.id === runId)}
            />
            {` · ${stats.total} scenario${stats.total === 1 ? "" : "s"}`}
            {identity?.repeats > 1 ? ` × ${identity.repeats} repeats` : ""}
            {(() => {
              /* Finishing what an earlier run left unstarted: a linked attempt of it. */
              const at = attemptHeaderOf(envState.runs || [], sourceRun);
              return at
                ? ` · Attempt ${at.attempt} of run ${at.of.ordinal} · ${(at.reason || "retry of interrupted trials").replace(/^./, (c) => c.toLowerCase())}`
                : "";
            })()}
            {(() => {
              /* A reviewed eligible subset: the requested count stays the denominator. */
              const c = (envState.runs || []).find(
                (r) => r.id === runId,
              )?.compat;
              return c && c.requested > c.evaluated
                ? ` · ${c.evaluated} evaluated / ${c.requested} requested`
                : "";
            })()}
            {(() => {
              /* A platform config that can change between runs. */
              const u = (envState.runs || []).find(
                (r) => r.id === runId,
              )?.unpinned;
              return u
                ? ` · unpinned on ${u.charAt(0).toUpperCase() + u.slice(1)}`
                : "";
            })()}
            {(() => {
              /* Tools answered from an earlier run's recording. */
              const of = (envState.runs || []).find(
                (r) => r.id === runId,
              )?.toolReplayOf;
              const src = of && (envState.runs || []).find((r) => r.id === of);
              return src ? ` · recorded-tool replay of run ${src.ordinal}` : "";
            })()}
            {finishedLabel ? ` · ${finishedLabel}` : ""}
            {/* Where it was started from, when that wasn't the console — the
                command on hover, so it can be re-run the same way. */}
            {sourceRun &&
              runViaOf(sourceRun).label !== "Console" &&
              (() => {
                const ci = ciOf(sourceRun);
                /* Re-run the same way: the same pinned configuration. */
                const cmd = `fi sim run --from-run ${sourceRun.id}`;
                return (
                  <>
                    {" · "}
                    <Tooltip
                      arrow
                      placement="bottom-start"
                      title={
                        <Box component="span" sx={{ fontFamily: "monospace" }}>
                          {cmd}
                        </Box>
                      }
                    >
                      <Box component="span" sx={{ cursor: "default" }}>
                        {ci
                          ? `From CI · PR #${ci.pr} · ${ci.branch} @ ${ci.sha}`
                          : `From ${runViaOf(sourceRun).label}`}
                      </Box>
                    </Tooltip>
                  </>
                );
              })()}
            {/* Our harness's misses — never red; the reason on hover. */}
            {report.summary?.health?.lost > 0 && (
              <>
                {" · "}
                <Tooltip
                  arrow
                  placement="bottom-start"
                  title={`${report.summary.health.note} A problem on our side, not your agent's.`}
                >
                  <Box component="span" sx={{ cursor: "default" }}>
                    {`${report.summary.health.lost} not scored`}
                  </Box>
                </Tooltip>
              </>
            )}
            {/* Tested invalid twice: out of every count; the rows are a click away. */}
            {pulledIds.length > 0 && (
              <>
                {" · "}
                <Tooltip
                  arrow
                  placement="bottom-start"
                  title="Tested invalid twice, so left out of every count. Click to see them."
                >
                  <Box
                    component="span"
                    {...clickable(
                      () => filterTo("Pulled for review", pulledIds),
                      {
                        role: "link",
                      },
                    )}
                    sx={{
                      ...focusRing,
                      cursor: "pointer",
                      "&:hover": {
                        color: "text.primary",
                        textDecoration: "underline",
                      },
                    }}
                  >
                    {`${pulledIds.length} pulled for review`}
                  </Box>
                </Tooltip>
              </>
            )}
            {/* What the agent could do here when the run started; the report on click. */}

            {/* The limit kept some from starting: finish them under a new
                limit — only those; the finished ones aren't run or charged again. */}
            {identity?.budget?.notStarted?.length > 0 &&
              (retryLeft.length > 0 ? (
                <>
                  {" · "}
                  <Tooltip
                    arrow
                    describeChild
                    placement="bottom-start"
                    title={
                      sourceRun?.status === "interrupted"
                        ? `Interrupted: ${sourceRun.interruption?.reason || "an infrastructure failure, not the agent."} Retry restarts only these; finished ones aren't charged again.`
                        : "Opens the run plan for just these scenarios, with a new limit. The ones that finished aren't run or charged again."
                    }
                  >
                    <Box
                      component="span"
                      {...clickable(
                        () =>
                          sourceRun?.status === "interrupted"
                            ? setRetryConfirm(true)
                            : navigate(
                                runPlanHref(envId, {
                                  only: retryLeft,
                                  trials: identity.repeats || 3,
                                  agent: identity.agentVersion,
                                  continues: identity.id,
                                }),
                              ),
                        { role: "link" },
                      )}
                      sx={{
                        ...focusRing,
                        cursor: "pointer",
                        "&:hover": {
                          color: "text.primary",
                          textDecoration: "underline",
                        },
                      }}
                    >
                      {sourceRun?.status === "interrupted"
                        ? `Interrupted · ${retryLeft.length} didn't start · ${retriedIds.length ? `Retry (attempt ${attemptsOf(sourceRun).length + 2})` : "Retry"}`
                        : `${retryLeft.length} didn't start · Run them`}
                    </Box>
                  </Tooltip>
                </>
              ) : (
                /* Everything that didn't start went into a linked attempt. */
                <>
                  {" · "}
                  <Box
                    component="span"
                    {...clickable(
                      () =>
                        navigate(
                          paths.dashboard.simulate.simulationRun(
                            envId,
                            lastAttempt.runId,
                          ),
                        ),
                      { role: "link" },
                    )}
                    sx={{
                      ...focusRing,
                      cursor: "pointer",
                      "&:hover": {
                        color: "text.primary",
                        textDecoration: "underline",
                      },
                    }}
                  >
                    {`${sourceRun?.status === "interrupted" ? "Interrupted · " : ""}${identity.budget.notStarted.length} didn't start · ${sourceRun?.status === "interrupted" ? "retried" : "continued"} as attempt ${lastAttempt.attempt}`}
                  </Box>
                </>
              ))}
            {/* What it metered, who placed the calls, which rule graded it and
                the replay header — one hover, not five clauses. */}
            <Tooltip
              classes={{ tooltip: "ds-hovercard" }}
              arrow
              placement="bottom-start"
              slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}
              title={
                <Box sx={{ py: 0.25, "& > div": { py: 0.125 } }}>
                  <div>
                    {report.metering?.line ? (
                      <MeteredText metering={report.metering} />
                    ) : (
                      "Nothing metered"
                    )}
                  </div>
                  <div>{`Caller ${runPinsOf(identity, env, envState).simulator}`}</div>
                  {identity?.secrets &&
                    Object.keys(identity.secrets).length > 0 && (
                      <div>{`Secrets · ${Object.keys(identity.secrets).join(", ")} — the references set when it ran`}</div>
                    )}
                  {report.summary?.grading && (
                    <div>
                      {report.summary.grading.rule >= 2
                        ? "Sub-goals decide pass or fail"
                        : report.summary.grading.label}
                    </div>
                  )}
                  <div>
                    {stubbed.length
                      ? `Replay header on every call · stubbed: ${stubbed.join(", ")}`
                      : `Replay header on every call (${REPLAY_HEADER})`}
                  </div>
                </Box>
              }
            >
              <Box
                component="span"
                sx={{
                  display: "inline-flex",
                  verticalAlign: "middle",
                  ml: 0.75,
                  cursor: "default",
                }}
              >
                <Iconify
                  icon="solar:info-circle-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
              </Box>
            </Tooltip>
          </Typography>
          {runCaps && (
            <Popover
              open={capsOpen}
              anchorEl={capsRef.current}
              onClose={() => setCapsOpen(false)}
              anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
              transformOrigin={{ vertical: "top", horizontal: "left" }}
              PaperProps={{ sx: { width: 560, mt: 0.5 } }}
            >
              <CapabilitySummary
                title="Capabilities when this run started"
                rows={runCaps.rows}
                requested={runCaps.requested}
                testedAt={runCaps.testedAt || null}
              />
            </Popover>
          )}
        </Box>
        <Tooltip arrow title={wideHeader ? "" : "Add evals"}>
          <Button
            variant="outlined"
            size="small"
            aria-label="Add evals"
            onClick={() => setAddingEvals(true)}
            startIcon={
              wideHeader ? (
                <Iconify icon="solar:add-circle-linear" width={16} />
              ) : undefined
            }
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
              ...(!wideHeader && { minWidth: 0, px: 0.75 }),
            }}
          >
            {wideHeader ? (
              "Add evals"
            ) : (
              <Iconify icon="solar:add-circle-linear" width={16} />
            )}
          </Button>
        </Tooltip>
        {/* The run's ID — what the CLI and SDK print and take, so a run
            started in the terminal and this page are one run. */}
        {/* Started from code (CLI, Python, CI, MCP): run it again the same way. */}
        {!trialMeta && sourceRun && runViaOf(sourceRun).label !== "Console" && (
          <CopyCommandButton
            command={`fi sim run --from-run ${sourceRun.id}`}
            label="Copy command"
          />
        )}
        {!trialMeta && runId && (
          <CopyIdButton
            id={runId}
            what="run ID"
            usedBy="fi sim runs results / rerun / compare take it"
          />
        )}
        {/* Share replaces the old Export: a read-only link, or the report as PDF. */}
        {/* An improvement's run is shared from its improvement, not on its own. */}
        {identity && !trialMeta && (
          <ShareMenu
            env={env}
            envState={envState}
            identity={identity}
            report={report}
            tasks={tasks}
            compact
          />
        )}
        {/* Re-run the whole run, in one of the two modes (§4.10.8). Selecting
            rows offers the same modes for just those scenarios. */}
        {!candidate &&
          !trialMeta &&
          (failingScenarioIds.length ? (
            /* What a reader re-runs after a failed run is the failures — one
             click; the whole-run modes sit behind the caret. */
            <Stack direction="row" sx={{ flexShrink: 0 }}>
              <Tooltip
                arrow
                title={`Re-run the ${failingScenarioIds.length} scenario${failingScenarioIds.length === 1 ? "" : "s"} that didn't pass`}
              >
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() =>
                    rerun(failingScenarioIds, {
                      mode: canSnapshot ? "snapshot" : "now",
                    })
                  }
                  startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
                  sx={{
                    color: "text.primary",
                    borderColor: "divider",
                    borderTopRightRadius: 0,
                    borderBottomRightRadius: 0,
                  }}
                >
                  Re-run failing
                </Button>
              </Tooltip>
              <Button
                variant="outlined"
                size="small"
                aria-label="More ways to repeat this run"
                onClick={(e) => setRunRerunAnchor(e.currentTarget)}
                sx={{
                  minWidth: 0,
                  color: "text.tertiary",
                  borderColor: "divider",
                  borderLeft: "none",
                  borderTopLeftRadius: 0,
                  borderBottomLeftRadius: 0,
                }}
              >
                <Iconify icon="solar:alt-arrow-down-linear" width={12} />
              </Button>
            </Stack>
          ) : (
            <Button
              variant="outlined"
              size="small"
              onClick={(e) => setRunRerunAnchor(e.currentTarget)}
              startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
              endIcon={
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width={12}
                  sx={{ color: "text.tertiary" }}
                />
              }
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Repeat
            </Button>
          ))}
        {/*
          The primary action after a failed run is not to run it again —
          the same agent against the same graders returns the same
          result. It is to summarise what went wrong so the reader can
          change the agent. Trial pages open the same drawer against the
          trial's own outcomes.
        */}
        {fixFirst ? (
          /* The next step from a fix's first run is the fix itself — a second
             Debug failures here would start a competing fix for the issue. */
          <Tooltip
            arrow
            title={`The fix for ${fixOfRun?.trigger?.issueId || "this issue"} is working on these failures.`}
          >
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={!fixOfRun}
                startIcon={<Iconify icon="fagi:fix" width={16} />}
                onClick={() =>
                  fixOfRun &&
                  navigate(improvementPath(env.id, fixOfRun.id), {
                    state: {
                      back: {
                        to: `${paths.dashboard.simulate.simulationRun(env.id, runId)}?tab=improvements`,
                        label: identity?.ordinal
                          ? `Run ${identity.ordinal}`
                          : "the run",
                      },
                    },
                  })
                }
              >
                View fix progress
              </Button>
            </span>
          </Tooltip>
        ) : candidate ? (
          /* Reads as unavailable rather than vanishing, so the reason — and
             the way to the improvement that owns this run — is findable. */
          <Tooltip
            arrow
            title={
              <>
                Candidate runs aren&apos;t analysed — open the improvement
                instead
                {named?.improvement?.href && (
                  <Box
                    component="span"
                    {...clickable(() => navigate(named.improvement.href), {
                      role: "link",
                    })}
                    sx={{
                      display: "block",
                      mt: 0.5,
                      fontWeight: 600,
                      cursor: "pointer",
                      textDecoration: "underline",
                      ...focusRing,
                    }}
                  >
                    {`Open ${named.improvement.name}`}
                  </Box>
                )}
              </>
            }
          >
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled
                startIcon={<Iconify icon="solar:magnifer-linear" width={16} />}
              >
                Debug failures
              </Button>
            </span>
          </Tooltip>
        ) : (
          <Tooltip
            arrow
            describeChild
            title={
              analysisCharge
                ? `Already analysed (${analysisCharge.credits.toLocaleString()} credits) — opening it again isn't charged.`
                : failingScenarioIds.length
                  ? `About ${analysisCredits.toLocaleString()} credits for ${failingScenarioIds.length} failing scenario${failingScenarioIds.length === 1 ? "" : "s"}. You confirm first.`
                  : "Nothing failed — nothing to analyse or charge."
            }
          >
            <Button
              variant="contained"
              color="primary"
              size="small"
              startIcon={<Iconify icon="solar:magnifer-linear" width={16} />}
              onClick={(e) =>
                /* The first open spends credits, so it asks first; opening
                   it again is free and goes straight in. */
                !analysisCharge && failingScenarioIds.length
                  ? setDebugConfirm(e.currentTarget)
                  : openDebug()
              }
            >
              Debug failures
            </Button>
          </Tooltip>
        )}
      </Stack>

      {/* ── came to improve: the environment was built from New improvement,
          and nothing has been improved in it yet ── */}
      {envState?.improveIntent &&
        !(envState.optimizations || []).length &&
        !fixFirst && (
          <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
            <NextStepBanner
              tone="info"
              title={
                failingScenarioIds.length
                  ? "Continue your improvement."
                  : "Nothing failed in this run."
              }
              line={
                failingScenarioIds.length
                  ? `${failingScenarioIds.length} scenario${failingScenarioIds.length === 1 ? "" : "s"} failed. Debug failures finds why, then starts the improvement from this run.`
                  : "There's nothing to improve from yet — add harder scenarios, or improve toward a goal from Improvements."
              }
              {...(failingScenarioIds.length && {
                actionLabel: "Debug failures",
                onAction: () => openDebug(),
              })}
              secondaryLabel="Not now"
              onSecondary={() => patch({ improveIntent: null })}
            />
          </Box>
        )}

      {/* ── a fix's first run: what it found, and what else it found ──
          A condition of this page, so a banner under the header (the header
          stays two lines). The fix itself is the header's button. */}
      {fixFirst && issueResult && (
        <Alert
          severity="info"
          icon={<Iconify icon="fagi:fix" width={16} />}
          /* In the page's gutter, like the tabs under it — never edge to edge.
             Neutral, not the blue info tint: it says what this run is for,
             and blue here read as "already fixed". */
          sx={{
            mx: 2,
            mt: 1.5,
            mb: 1,
            flexShrink: 0,
            bgcolor: "background.neutral",
            color: "text.primary",
            border: "1px solid",
            borderColor: "divider",
            "& .MuiAlert-icon": { color: "text.secondary" },
          }}
          action={
            failingScenarioIds.length > 0 ? (
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={() => openDebug()}
                sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
              >
                {!analysisCharge
                  ? `Debug failures · ≈ ${analysisCredits.toLocaleString()} credits`
                  : "Debug failures"}
              </Button>
            ) : null
          }
        >
          <Box component="span" sx={{ fontWeight: 600 }}>
            {`Step 1 of the fix for ${fixIssueId}. `}
          </Box>
          {issueResult.reproduced > 0
            ? `The issue showed up (${issueResult.reproduced} of ${issueResult.total}); the fix is now working on it.`
            : issueResult.inconclusive
              ? `The issue didn't fail reliably (${issueResult.inconclusive} of ${issueResult.total} failed only once), so the fix stopped.`
              : "The issue didn't show up on today's agent, so there was nothing to fix."}
          {failingScenarioIds.length > 0 && (
            <Box component="span" sx={{ display: "block" }}>
              <Box component="span" sx={{ fontWeight: 600 }}>
                Also found:
              </Box>
              {` ${failingScenarioIds.length} other ${failingScenarioIds.length === 1 ? "failure" : "failures"}, not part of this issue — the fix leaves ${failingScenarioIds.length === 1 ? "it" : "them"} alone.`}
            </Box>
          )}
        </Alert>
      )}

      {/* ── the run group it was started with, if any ── */}
      <RunGroupBar envId={envId} runId={runId} />

      {/* ── re-grade: a grader got fixed since this run (AC-12.23, AC-16.11) ── */}
      {(regrade || sourceRun?.regradedWith) && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 3,
            py: 0.875,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:restart-linear"
            width={16}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {regrade ? (
              <>
                <Box
                  component="span"
                  sx={{ fontWeight: 600, color: "text.primary" }}
                >
                  Test repaired since this run.{" "}
                </Box>
                {[
                  regrade.subGoals.length
                    ? `${regrade.subGoals.length === 1 ? "A sub-goal" : `${regrade.subGoals.length} sub-goals`} this run used got a grader fix`
                    : null,
                  regrade.evalTo
                    ? `evals moved ${regrade.evalFrom} → ${regrade.evalTo}`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" and ")
                  .replace(/^./, (c) => c.toUpperCase())}
                {`. Re-grade it so its scores use the repaired test — ${regrade.stateSaved ? "the stored traces and end-states, read again" : "the stored traces, read again (the end state wasn't saved, so only the conversation checks are re-graded)"}: ${regrade.scenarioIds.length} scenarios, no new calls. The earlier result is kept. What that changes is the test's, not the agent's.`}
              </>
            ) : latestRegrade.toolCalls ? (
              /* Tool call evaluation: what it caught, said plainly. */
              (() => {
                const caught = tasks.filter(
                  (t) => t.regraded?.toolCalls,
                ).length;
                return `Re-evaluated · previous result kept. ${caught} ${caught === 1 ? "scenario" : "scenarios"} claimed a tool call that never happened and now fail${regradedChanged ? ` · ${regradedChanged} ${regradedChanged === 1 ? "verdict" : "verdicts"} turned from pass to fail` : ""}.`;
              })()
            ) : (
              `Re-evaluated · previous result kept. Re-graded with ${latestRegrade.label} · ${regradedChanged} ${regradedChanged === 1 ? "verdict" : "verdicts"} changed under the repaired test.${latestRegrade.conversationOnly ? " Only conversation checks were re-graded." : ""}`
            )}
            {showEarlier && (
              <Box component="span" sx={{ display: "block", mt: 0.5 }}>
                {regradeHistory.map((g, i) => {
                  const b = g.before;
                  return (
                    <Box
                      key={g.at || i}
                      component="span"
                      sx={{ display: "block" }}
                    >
                      {`${i === 0 ? "Original result" : `Before re-grade ${i + 1}`}: ${b ? `${b.passed} passed · ${b.failed} failed${b.flaky ? ` · ${b.flaky} flaky` : ""}${b.unmeasured ? ` · ${b.unmeasured} not counted` : ""}` : "not recorded"} — replaced ${new Date(g.at).toLocaleDateString(undefined, { month: "short", day: "numeric" })} by ${g.label}${g.summary ? ` (${g.summary})` : ""}`}
                    </Box>
                  );
                })}
              </Box>
            )}
          </Typography>
          {!regrade && regradeHistory.length > 0 && (
            <Button
              size="small"
              variant="text"
              onClick={() => setShowEarlier((v) => !v)}
              sx={{
                color: "text.secondary",
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              {showEarlier ? "Hide earlier result" : "Earlier result"}
            </Button>
          )}
          {regrade && (
            <Button
              size="small"
              variant="outlined"
              onClick={startRegrade}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              {`${regrade.label} — ≈ ${regrade.credits.toLocaleString()} credits`}
            </Button>
          )}
        </Stack>
      )}

      {/* A run an improvement made: kept or not, and what it did vs where it started. */}
      <ImprovementRunStrip
        env={env}
        envState={envState}
        runId={runId}
        onOpenSearch={() => {
          setFixOptId(
            improvementOf({ id: runId }, envState)?.record?.id || null,
          );
          setFixOpen(true);
        }}
      />

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2, pt: 0.5 }}>
          {/* ── tabs ── */}
          <CustomTabs
            value={tab}
            onChange={(_, v) => {
              setTab(v);
              /* The tab lives in the URL, so Back from an improvement (or a
                 reload) lands on the same tab. */
              const next = new URLSearchParams(params);
              next.set("tab", v);
              setParams(next, { replace: true, state: location.state });
            }}
            sx={{
              boxShadow: "none",
              borderBottom: "1px solid",
              borderColor: "divider",
              mb: 2,
            }}
          >
            {SHOW_INSIGHTS && <Tab value="insights" label="Insights" />}
            <Tab value="tasks" label={`Scenarios (${stats.total})`} />
            <Tab value="analytics" label="Analytics" />
            <Tab value="cost" label="Cost" />
            <Tab value="evaluate" label="Evaluate" />
            {!SHOW_INSIGHTS && <Tab value="insights" label="Insights" />}
            <Tab
              value="improvements"
              label={
                improvedFrom ? `Improvements (${improvedFrom})` : "Improvements"
              }
            />
          </CustomTabs>

          {/* Wraps both tab bodies so it survives a tab switch: errors thrown
              while one tab unmounts land here, not on the app-root boundary. */}
          <ErrorBoundary
            resetKeys={[tab]}
            onError={(error, info) =>
              logger.error("[RunResults tab]", error, info?.componentStack)
            }
            fallbackRender={({ error, resetErrorBoundary }) => (
              <SectionCard sx={{ p: 3 }}>
                <Stack spacing={1.5}>
                  <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                    This tab failed to render
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.tertiary",
                      fontFamily: "monospace",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {String(error?.stack || error?.message || error)
                      .split("\n")
                      .slice(0, 4)
                      .join("\n")}
                  </Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={resetErrorBoundary}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    Retry
                  </Button>
                </Stack>
              </SectionCard>
            )}
          >
            {tab === "tasks" && (
              <SectionCard
                bare
                /*
                Grow to fill the remaining vertical space so the traces
                card reaches the bottom of the viewport rather than
                sizing to its rows. Values tuned so the card starts
                just below the tab strip and ends at the viewport edge.
              */
                /* The traces card fills the viewport below the tab strip and
                 the rows scroll INSIDE it — so the toolbar (Group by, Filter,
                 Clear, Re-run N…) and the column headers stay put while the
                 reader walks the rows. `overflow: hidden` on the outer box
                 traps the row scroll inside, and the flex column lets the
                 table area grow to fill what's left after the toolbar. */
                sx={{
                  height: "calc(100vh - 152px)",
                  display: "flex",
                  flexDirection: "column",
                }}
                /*
                The title used to be "Task traces" plus a one-liner
                subtitle. The header is now the two table controls: the
                Group by picker sits on the left in place of the title
                and reads as the primary axis; the Columns picker sits
                on the right in the action slot. Selection actions
                (Clear / Re-run N) override both when rows are ticked
                — that's the mode change and it should occupy the
                whole header.
              */
                title={
                  selected.size ? (
                    <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                      {selected.size} selected
                    </Typography>
                  ) : (
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      sx={{
                        minWidth: 0,
                        "& > *:not(:last-child)": { flexShrink: 0 },
                      }}
                    >
                      <TraceGroupByPicker
                        value={groupBy}
                        onChange={setGroupByReconciled}
                        multiAgent={!!memberResult}
                      />
                      {groupBy !== "none" && (
                        <Stack
                          component="label"
                          direction="row"
                          alignItems="center"
                          spacing={0.5}
                          sx={{
                            cursor: "pointer",
                            pr: 0.5,
                            userSelect: "none",
                          }}
                        >
                          <Switch
                            size="small"
                            checked={allOpen}
                            onChange={(e) =>
                              setExpandCommand({
                                open: e.target.checked,
                                at: Date.now(),
                              })
                            }
                          />
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              whiteSpace: "nowrap",
                            }}
                          >
                            Expand all
                          </Typography>
                        </Stack>
                      )}
                      {/*
                      Filter button — same shape as Group by / Columns
                      so the three read as a single control row. Opens
                      the shared platform FilterPanel popover so the
                      Basic / Query / AI tabs and their chrome match
                      Evals and Datasets.
                    */}
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={(e) => setFilterAnchor(e.currentTarget)}
                        startIcon={
                          <Iconify
                            icon="solar:filter-linear"
                            width={16}
                            sx={{
                              color: filterCount
                                ? "primary.main"
                                : "text.tertiary",
                            }}
                          />
                        }
                        endIcon={
                          <Iconify
                            icon="solar:alt-arrow-down-linear"
                            width={12}
                            sx={{ color: "text.tertiary" }}
                          />
                        }
                        sx={{
                          color: "text.primary",
                          borderColor: "divider",
                          "&:hover": {
                            borderColor: "text.disabled",
                            bgcolor: "transparent",
                          },
                        }}
                      >
                        Filter
                        {filterCount > 0 && (
                          <>
                            <Box
                              component="span"
                              sx={{
                                mx: 0.5,
                                color: "text.tertiary",
                                fontWeight: 400,
                              }}
                            >
                              ·
                            </Box>
                            <Box
                              component="span"
                              sx={{ color: "primary.main" }}
                            >
                              {filterCount}
                            </Box>
                          </>
                        )}
                      </Button>
                      {/* The filter a link applied (a sub-goal, an issue, a count):
                        one chip the height of the buttons, truncated — the full
                        label on hover — so a long name never reflows the bar. */}
                      {bucketFilter && (
                        <Tooltip
                          arrow
                          title={`Filtered · ${bucketFilter.label}`}
                        >
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={0.75}
                            sx={{
                              height: 32,
                              pl: 1,
                              pr: 0.5,
                              borderRadius: "4px",
                              minWidth: 0,
                              maxWidth: 360,
                              border: "1px solid",
                              borderColor: (t) =>
                                alpha(t.palette.primary.main, 0.4),
                              bgcolor: "status.discovery.bg",
                            }}
                          >
                            <Iconify
                              icon="solar:filter-linear"
                              width={14}
                              sx={{ color: "primary.main", flexShrink: 0 }}
                            />
                            <Typography
                              noWrap
                              sx={{
                                typography: "s2",
                                fontWeight: 600,
                                color: "primary.main",
                                minWidth: 0,
                              }}
                            >
                              {bucketFilter.label}
                            </Typography>
                            <IconButton
                              size="small"
                              aria-label="Clear filter"
                              onClick={() => setBucketFilter(null)}
                              sx={{ flexShrink: 0 }}
                            >
                              <Iconify
                                icon="solar:close-linear"
                                width={16}
                                sx={{ color: "text.tertiary" }}
                              />
                            </IconButton>
                          </Stack>
                        </Tooltip>
                      )}
                    </Stack>
                  )
                }
                action={
                  selected.size ? (
                    <Stack direction="row" spacing={1}>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => setSelected(new Set())}
                      >
                        Clear
                      </Button>
                      {/* Re-run opens a small popover with two choices: rerun
                        the calls (a new simulation), or score these scenarios
                        against new evals without re-running the calls. The
                        second one is the cheap version — no LLM cost — and it
                        is the one people reach for when they added a grader
                        after the fact. */}
                      {/* The main button is Debug when failures are picked;
                          Re-run is the main one otherwise. */}
                      <Button
                        variant={
                          selectedFailingIds.length ? "outlined" : "contained"
                        }
                        color={
                          selectedFailingIds.length ? "inherit" : "primary"
                        }
                        size="small"
                        onClick={(e) => setRerunAnchor(e.currentTarget)}
                        sx={
                          selectedFailingIds.length
                            ? { color: "text.primary", borderColor: "divider" }
                            : undefined
                        }
                        startIcon={
                          <Iconify icon="solar:refresh-bold" width={16} />
                        }
                        endIcon={
                          <Iconify
                            icon="solar:alt-arrow-down-linear"
                            width={12}
                          />
                        }
                      >
                        Re-run {selected.size}
                      </Button>
                      {selectedFailingIds.length > 0 && (
                        <Tooltip
                          arrow
                          title={`Diagnoses only the ${selectedFailingIds.length} failing ${selectedFailingIds.length === 1 ? "scenario" : "scenarios"} you picked. ${RATE_PLAN.rates.analysis_calls.credits} credits each, once.`}
                        >
                          <Button
                            variant="contained"
                            color="primary"
                            size="small"
                            onClick={() => openDebug(selectedFailingIds)}
                          >
                            {`Debug ${selectedFailingIds.length} selected`}
                          </Button>
                        </Tooltip>
                      )}
                    </Stack>
                  ) : (
                    <Stack direction="row" alignItems="center" spacing={1.5}>
                      <StatusFilterChips
                        value={statusChip}
                        counts={statusCounts}
                        onChange={setStatusChip}
                        blocked={incompatibleChips}
                      />
                      <TraceColumnsPicker
                        value={visibleColumns}
                        onChange={setVisibleColumns}
                        evals={shownEvals}
                      />
                    </Stack>
                  )
                }
              >
                {/* A failure grouping (sub-goal / pattern) drops passed and
                  unmeasured tasks — with those filtered in and nothing failing,
                  `shown` is non-empty but zero groups render. Detect that and
                  show a real empty state instead of a blank table. */}
                {/* Test invalid twice: pulled from this run, flagged for review,
                  and left out of every count — a small group of its own. */}
                {groupBy === "member" && entryMember && (
                  <Typography
                    sx={{
                      typography: "s2",
                      color: "text.tertiary",
                      mb: 1.5,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {delegationEvidenceOf(envState?.agent).observed
                      ? `${entryMember.member.name} handed on ${memberResult.handoffs} of ${memberResult.total} conversations.`
                      : `Declared, not observed — ${envState?.agent?.name || "this agent"} sends no delegation events. Only the overall result counts.`}
                  </Typography>
                )}
                {groupableShown.length === 0 ? (
                  <EmptyState
                    icon="solar:filter-linear"
                    title={
                      failureGrouping && shown.length > 0
                        ? "Nothing to group here"
                        : "No scenarios match that filter"
                    }
                    body={
                      failureGrouping && shown.length > 0
                        ? "This grouping only shows failing runs — none of the matching runs failed. Switch the status filter or group by Goal."
                        : undefined
                    }
                  />
                ) : (
                  <TraceTable
                    tasks={
                      verdicts
                        ? tableShown.map((t) => ({
                            ...t,
                            verdictCause: verdicts.get(t.id) || null,
                          }))
                        : tableShown
                    }
                    evals={shownEvals}
                    selected={selected}
                    groupBy={groupBy}
                    members={memberList}
                    attributionObserved={
                      delegationEvidenceOf(envState?.agent).observed
                    }
                    columns={visibleColumns}
                    env={env}
                    onToggle={toggle}
                    onToggleAll={() =>
                      setSelected((prev) =>
                        shown.every((t) => prev.has(t.id))
                          ? new Set()
                          : new Set(shown.map((t) => t.id)),
                      )
                    }
                    onOpen={(t) => {
                      setEvalFocus(null);
                      setOpenTask(t);
                    }}
                    onOpenTrace={openTrace}
                    onOpenEval={(t, r) => {
                      setEvalFocus(r.name);
                      setOpenTask(t);
                    }}
                    onRerunEval={rerunEvalColumn}
                    onHideEval={hideEvalColumn}
                    onEditEval={editEvalColumn}
                    rescoringEvalId={rescoringEval}
                    expandCommand={expandCommand}
                    onAllOpenChange={setAllOpen}
                    gradingBasis={report.summary?.grading?.basis || null}
                  />
                )}
              </SectionCard>
            )}

            {SHOW_INSIGHTS && tab === "insights" && (
              <InsightsTab
                env={env}
                envState={envState}
                tasks={tasks}
                run={identity || { id: runId }}
                history={summaries}
                goalOf={deriveUseCaseLabel}
                tracing={tracing}
                candidate={candidate}
                existingFixes={existingFixes}
                insights={report.insights}
                summary={report.summary}
                onSetBar={setReportBar}
                record={record}
                onFilter={filterTo}
                onOpenTrace={openTrace}
                onFix={fixIssue}
                onOpenFix={(optId) => {
                  setFixOptId(optId);
                  setFixOpen(true);
                }}
              />
            )}

            {tab === "improvements" && (
              <ImprovementSourcesList
                run={{
                  envId: env.id,
                  runId,
                  label: identity?.ordinal
                    ? `Run ${identity.ordinal}`
                    : "this run",
                }}
              />
            )}

            {tab === "cost" && (
              <CostTab
                run={identity}
                env={env}
                envState={envState}
                tasks={tasks}
                onOpenTask={(t) => {
                  /* The full call drawer, as a Scenarios row opens it. */
                  if (isVoiceRun) {
                    setEvalFocus(null);
                    setOpenTask(t);
                  } else openTrace(t);
                }}
              />
            )}

            {!SHOW_INSIGHTS && tab === "insights" && (
              <RunInsightsTab
                envState={envState}
                patch={patch}
                runId={runId}
                runLabel={
                  identity?.ordinal ? `Run ${identity.ordinal}` : "This run"
                }
                tasks={tasks}
                insights={report.insights}
                debug={
                  candidate || fixFirst
                    ? null
                    : {
                        /* Insights finds the pattern; Debug failures reads
                           those failures step by step and proposes the
                           change — narrowed to the finding's trials. */
                        scenarioIdsOf: (taskIds) => [
                          ...new Set(
                            tasks
                              .filter(
                                (t) =>
                                  taskIds.includes(t.id) &&
                                  (t.status === "failed" ||
                                    t.status === "flaky"),
                              )
                              .map((t) => t.scenarioId || t.id),
                          ),
                        ],
                        creditsOf: (scenarioIds) =>
                          scenarioIds.filter(
                            (id) => !(analysisCharge?.ids || []).includes(id),
                          ).length * RATE_PLAN.rates.analysis_calls.credits,
                        open: (scenarioIds) => openDebug(scenarioIds),
                        improve: (scenarioIds) => openImprove(scenarioIds),
                      }
                }
                onOpenTask={(t) => {
                  if (isVoiceRun) {
                    setEvalFocus(null);
                    setOpenTask(t);
                  } else openTrace(t);
                }}
              />
            )}

            {tab === "evaluate" && (
              <EvaluateTab
                env={env}
                envState={envState}
                patch={patch}
                runId={runId}
                runLabel={
                  identity?.ordinal ? `Run ${identity.ordinal}` : "This run"
                }
                tasks={tasks}
                others={otherRuns}
                voice={isVoiceRun}
                worldHeld={!isConnectOnly(env)}
                filter={currentFilter}
                selectedIds={selectedIds}
                onOpenTask={(t) => {
                  if (isVoiceRun) {
                    setEvalFocus(null);
                    setOpenTask(t);
                  } else openTrace(t);
                }}
              />
            )}

            {tab === "analytics" && (
              <RunAnalytics
                tasks={tasks}
                evals={shownEvals}
                env={env}
                stats={stats}
                runHistory={summaries}
                currentRunId={runId}
              />
            )}
          </ErrorBoundary>
        </Box>
      </Box>

      {/*
        Shared platform filter popover. Mounted at the RunResults root
        so the popover's positioning frame is not affected by the
        scrolling body below.
      */}
      <ObjectFilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={applyFilters}
        aiPlaceholder="Ask AI — e.g. 'show adversarial traces that failed identity'"
      />

      {/*
        Adding a grader applies to the environment, so it holds for every run
        from here — and the run that just finished was not graded on it, which
        is why this offers the re-run rather than silently changing the numbers
        above.
      */}
      {editingEval && (
        <EditEvalDrawer
          item={editingEval}
          env={env}
          envState={envState}
          onClose={() => setEditingEvalId(null)}
          onSave={(changes) => updateAppliedEval(editingEval.id, changes)}
        />
      )}

      <AddEvalsDrawer
        open={addingEvals}
        onClose={() => setAddingEvals(false)}
        env={env}
        envState={envState}
        existingIds={envState.evals}
        slotsLeft={evalSlotsLeft}
        onAdd={(added) => {
          /* Through the environment's own evals: the change is recorded (a
             new environment version once runs exist) and capped at 8. The
             run on screen wasn't graded on it — a re-run is offered, never
             started for the user. */
          const nextLabel = (envState.runs || []).length
            ? nextSnapshotLabel(env, envState)
            : null;
          const { added: n, dropped } = addAppliedEvals(added);
          setAddingEvals(false);
          if (!n) {
            enqueueSnackbar(
              dropped
                ? "No room — an environment holds 8 evaluations at most"
                : "Already applied",
              { variant: "info" },
            );
            return;
          }
          enqueueSnackbar(
            [
              `Added ${n} evaluation${n === 1 ? "" : "s"}${nextLabel ? ` · environment ${nextLabel}` : ""}`,
              dropped ? `${dropped} left out — 8 at most` : null,
              "this run wasn't graded on it",
            ]
              .filter(Boolean)
              .join(" · "),
            {
              variant: "success",
              autoHideDuration: 10000,
              action: (key) => (
                <Button
                  variant="outlined"
                  size="small"
                  color="inherit"
                  onClick={() => {
                    closeSnackbar(key);
                    rerun();
                  }}
                >
                  Re-run
                </Button>
              ),
            },
          );
        }}
      />

      {/*
        Adhoc evals — pick graders to score just the scenarios that were
        selected on the run screen. The picker filters out evals that are
        already columns (there's no point re-scoring a column that has
        every row filled), and adding them stays local to this run.
      */}
      <AddEvalsDrawer
        open={addingAdhocEvals}
        onClose={() => setAddingAdhocEvals(false)}
        env={env}
        envState={envState}
        existingIds={shownEvals.map((e) => e.id)}
        onAdd={(added) => {
          /* Read from what the run saved, like every evaluation: each one is
             kept with this run under its own id, its version and the trials
             it read; the ones it couldn't read, and the ones the judge
             didn't answer, are counted apart. */
          const runLabel = identity?.ordinal
            ? `Run ${identity.ordinal}`
            : "This run";
          const ctx = {
            runId,
            runLabel,
            tasks,
            others: [],
            voice: isVoiceRun,
            worldHeld: !isConnectOnly(env),
          };
          const ids = new Set(adhocIds);
          const trials = wrapTrials(runId, runLabel, tasks, runId).filter((t) =>
            ids.has(t.id),
          );
          const records = added.map((ev) => {
            const known = getEval(ev.id);
            const mapping = Object.fromEntries(
              Object.entries(ev.mapping || {}).map(([k, v]) => [
                k,
                evidenceOfSource(v) || "env",
              ]),
            );
            return runEvaluation({
              evaluation: {
                id: ev.id,
                name: ev.instanceName || ev.name || known?.name || ev.id,
                version: evaluationVersionOf(ev, envState),
                threshold: known?.threshold ?? ev.threshold ?? 0.8,
                icon: ev.icon || known?.icon,
              },
              mapping: Object.keys(mapping).length
                ? mapping
                : { output: "transcript" },
              trials,
              ctx,
              scope: "selected",
              scopeLabel: `Selected trials (${trials.length})`,
              source: "scenarios",
            });
          });
          patch({
            trialEvals: {
              ...(envState.trialEvals || {}),
              [runId]: [...[...records].reverse(), ...(savedResults || [])],
            },
          });
          records.forEach((r) =>
            enqueueSnackbar(
              `${r.evaluator.name} ${r.evaluator.version} · ${r.id} · ${denominatorLine(evaluationCountsOf(r))}`,
              { variant: "info", autoHideDuration: 8000 },
            ),
          );
          setAddingAdhocEvals(false);
          setSelected(new Set());
          setAdhocIds([]);
        }}
      />

      {/* ── re-run chooser ── */}
      {/* Debug failures, first open: what it reads and what it costs, then Start. */}
      <Popover
        open={!!debugConfirm}
        anchorEl={debugConfirm}
        onClose={() => setDebugConfirm(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: { sx: { mt: 0.75, width: 320, borderRadius: "8px" } },
        }}
      >
        <Box sx={{ p: 1.5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 500 }}>
            {`Debug ${failingScenarioIds.length} failing scenario${failingScenarioIds.length === 1 ? "" : "s"}`}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
          >
            Reads each failure step by step, finds where it went wrong and
            proposes the change to try.
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}>
            {`About ${analysisCredits.toLocaleString()} credits · ${RATE_PLAN.rates.analysis_calls.credits} per scenario, charged once.`}
          </Typography>
          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{ mt: 1.5 }}
          >
            <Button
              size="small"
              onClick={() => setDebugConfirm(null)}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                setDebugConfirm(null);
                openDebug();
              }}
            >
              Start
            </Button>
          </Stack>
        </Box>
      </Popover>
      <Popover
        open={!!rerunAnchor}
        anchorEl={rerunAnchor}
        onClose={() => setRerunAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: { sx: { mt: 0.75, minWidth: 340, borderRadius: "8px" } },
        }}
      >
        <Box sx={{ px: 2, pt: 1.75, pb: 1 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
            }}
          >
            Re-run {selected.size} scenario{selected.size === 1 ? "" : "s"}
          </Typography>
        </Box>
        <RerunModes
          run={sourceRun}
          canSnapshot={canSnapshot}
          envLabel={
            sourceRun ? runIdentityOf(sourceRun, envState, env).env : null
          }
          agents={agentVersions(envState).map((v) => v.label)}
          onRun={(mode, agent) => {
            const ids = [...selected];
            setRerunAnchor(null);
            rerun(ids, { mode, agent });
          }}
        />
        <MenuItem
          onClick={() => {
            setAdhocIds([...selected]);
            setRerunAnchor(null);
            setAddingAdhocEvals(true);
          }}
          sx={{ alignItems: "flex-start", gap: 0.75, py: 1.25 }}
        >
          <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
            <Iconify
              icon="solar:checklist-minimalistic-linear"
              width={16}
              sx={{ color: "text.primary" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Run evals on these scenarios"
            secondary="No calls are made — pick evaluations to score these rows. Results are saved with this run."
            primaryTypographyProps={{ typography: "s2", fontWeight: 600 }}
            secondaryTypographyProps={{
              typography: "s3",
              color: "text.tertiary",
              sx: { whiteSpace: "normal" },
            }}
          />
        </MenuItem>
      </Popover>

      {/* ── re-run the whole run ── */}
      <Popover
        open={!!runRerunAnchor}
        anchorEl={runRerunAnchor}
        onClose={() => setRunRerunAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.75,
              width: 420,
              maxHeight: "calc(100vh - 120px)",
              borderRadius: "8px",
            },
          },
        }}
      >
        <Box sx={{ px: 2, pt: 1.75, pb: 1 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
            }}
          >
            {`Repeat ${identity ? `run ${identity.ordinal}` : "this run"} · ${stats.total} scenario${stats.total === 1 ? "" : "s"}`}
          </Typography>
        </Box>
        {/* ── nothing runs again ── */}
        <RepeatGroup>No new agent calls</RepeatGroup>
        <RepeatRow
          icon="solar:play-circle-linear"
          action={repeat.replay}
          onClick={() => {
            setRunRerunAnchor(null);
            setOpenPane("transcript");
            setOpenTask(
              tasks.find((t) => t.status === "failed") || tasks[0] || null,
            );
          }}
        />
        <RepeatRow
          icon="solar:checklist-minimalistic-linear"
          action={repeat.reeval}
          onClick={() => {
            setRunRerunAnchor(null);
            if (regrade) return startRegrade();
            setAdhocIds(tasks.map((t) => t.id));
            return setAddingAdhocEvals(true);
          }}
        />
        {/* ── the agent runs again ── */}
        <RepeatGroup>New agent calls</RepeatGroup>
        <RerunModes
          run={sourceRun}
          canSnapshot={canSnapshot}
          envLabel={
            sourceRun ? runIdentityOf(sourceRun, envState, env).env : null
          }
          agents={agentVersions(envState).map((v) => v.label)}
          onRun={(mode, agent) => {
            setRunRerunAnchor(null);
            rerun(sourceRun?.partial ? sourceRun.scenarioIds : null, {
              mode,
              agent,
            });
          }}
        />
        <RepeatRow
          icon="solar:restart-linear"
          action={repeat.failures}
          onClick={() => {
            setRunRerunAnchor(null);
            rerun(repeat.failures.ids, {
              mode: canSnapshot ? "snapshot" : "now",
            });
          }}
        />
        <RepeatRow
          icon="solar:play-stream-linear"
          action={repeat.recorded}
          onClick={() => {
            setRunRerunAnchor(null);
            rerun(sourceRun?.partial ? sourceRun.scenarioIds : null, {
              mode: "snapshot",
              toolReplay: sourceRun?.id,
            });
          }}
        />
        {/* ── unfinished work ── */}
        <RepeatGroup>Unfinished work</RepeatGroup>
        <RepeatRow
          icon="solar:play-linear"
          action={repeat.resume}
          onClick={() => {
            setRunRerunAnchor(null);
            setRetryConfirm(true);
          }}
        />
        <RepeatRow icon="solar:history-linear" action={repeat.checkpoint} />
        <RepeatRow
          icon="solar:branching-paths-up-linear"
          action={repeat.fork}
        />
        {/* The same re-runs from code. */}
        {runId && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{
              px: 1.5,
              py: 1,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flex: 1,
                pl: 0.5,
              }}
            >
              From code
            </Typography>
            {failingScenarioIds.length > 0 && (
              <CopyCommandButton
                command={`fi sim run --from-run ${runId} --failed-only`}
                label="Copy failing only"
              />
            )}
            <CopyCommandButton
              command={`fi sim run --from-run ${runId}`}
              label="Copy whole run"
            />
          </Stack>
        )}
      </Popover>

      {/* ── retry interrupted: what restarts, and what could act twice ── */}
      {retryConfirm &&
        repeat.resume?.ids?.length > 0 &&
        (() => {
          const ids = repeat.resume.ids;
          const titleOf = (id) =>
            (envState?.scenarios || []).find((x) => x.id === id)?.title || id;
          const real = proofKindOf(envState?.agent).kind === "live";
          return (
            <ConfirmDialog
              open
              onClose={() => setRetryConfirm(false)}
              PaperProps={{ sx: { maxWidth: 520 } }}
              title={`Retry ${ids.length} interrupted?`}
              content={
                <Stack spacing={1.25}>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {`These restart from their seed. The ${(sourceRun?.scenarioIds || []).filter((id) => !ids.includes(id)).length} that finished aren't charged again.`}
                  </Typography>
                  <Box
                    component="ul"
                    sx={{
                      m: 0,
                      pl: 2.5,
                      typography: "s2",
                      color: "text.primary",
                    }}
                  >
                    {ids.slice(0, 8).map((id) => (
                      <li key={id}>{titleOf(id)}</li>
                    ))}
                    {ids.length > 8 && (
                      <Box component="li" sx={{ color: "text.secondary" }}>
                        {`and ${ids.length - 8} more`}
                      </Box>
                    )}
                  </Box>
                  {/* Unsafe to repeat: an action that reached a real system
                      blocks automatic retry (runs.md). */}
                  {real && (
                    <Typography
                      sx={{ typography: "s2", color: "status.warning.fg" }}
                    >
                      Retry is off for this run. Its agent&apos;s tools reach
                      your real systems, so a trial cut off partway may have
                      already sent an email or changed a record — retrying would
                      do it again. Switch the environment&apos;s runs to Sealed,
                      then run these as a new run.
                    </Typography>
                  )}
                </Stack>
              }
              action={
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={real}
                  onClick={() => {
                    setRetryConfirm(false);
                    navigate(
                      runPlanHref(envId, {
                        only: ids,
                        trials: sourceRun?.repeats || 3,
                        agent: sourceRun?.agentVersion,
                        continues: sourceRun?.id,
                      }),
                    );
                  }}
                >
                  {`Retry ${ids.length}`}
                </Button>
              }
            />
          );
        })()}

      {/* ── trace drawer ── */}
      {/*
        Wide, because it is two panes: the call on the left and what was
        measured about it on the right. Prev/next walk the filtered list, so
        reading four failures in a row does not mean closing and reopening.
      */}
      {/* ── fix my agent ── */}
      <FixMyAgentDrawer
        open={fixOpen}
        openSetup={fixSetup}
        onClose={() => {
          setFixOpen(false);
          setFixOptId(null);
          setDebugIds(null);
          setFixSetup(false);
          /* Drop ?improve so "From Improvement run N" opens it again, and Back
             doesn't stop on the same run with the drawer shut. */
          if (params.get("improve")) {
            const next = new URLSearchParams(params);
            next.delete("improve");
            setParams(next, {
              replace: true,
              state: fromIssue ? { fromIssue } : location.state,
            });
          }
        }}
        openOptimizationId={fixOptId}
        env={env}
        envState={envState}
        patch={patch}
        addAgentVersion={addAgentVersion}
        tasks={drawerTasks}
        picked={
          debugIds
            ? {
                count: debugIds.length,
                leftOut: failingScenarioIds.length - debugIds.length,
              }
            : null
        }
        stats={stats}
        runId={runId}
        onOpenTask={(t, step) => {
          setFocusStep(step || null);
          setOpenTask(t);
        }}
        onViewIssue={(label, ids) => {
          /* Filter the trace table to the exact task ids the finding
             addresses and jump to Test runs — but keep the debug drawer
             open so the user can compare the finding against the calls
             side-by-side without having to reopen it. */
          const idSet = new Set(ids);
          setBucketFilter({ label, match: (t) => idSet.has(t.id) });
          setTab("tasks");
        }}
      />

      {(() => {
        const closeTask = () => {
          setOpenTask(null);
          setFocusStep(null);
          setEvalFocus(null);
          setOpenPane(null);
        };
        const idx = openTask
          ? shown.findIndex((t) => t.id === openTask.id)
          : -1;
        const goPrev = idx > 0 ? () => setOpenTask(shown[idx - 1]) : undefined;
        const goNext =
          idx > -1 && idx < shown.length - 1
            ? () => setOpenTask(shown[idx + 1])
            : undefined;

        /* A voice run gets the exact drawer the Observe screen uses for a
           voice call — the real product component, fed a `data` shape by the
           adapter, not a bespoke rebuild. Every other surface keeps the
           existing call drawer. */
        if (isVoiceRun) {
          const traceIdx = traceTask
            ? shown.findIndex((t) => t.id === traceTask.id)
            : -1;
          return (
            <>
              {/* The trace on its own — opened from Insights at the offending span. */}
              <SideDrawer
                open={!!traceTask}
                onClose={() => setTraceTask(null)}
                width={{ xs: "100%", md: 960 }}
              >
                {traceTask && (
                  <TraceSheet
                    showCost={tab === "cost"}
                    task={traceTask}
                    env={env}
                    envState={envState}
                    onClose={() => setTraceTask(null)}
                    onPrev={
                      traceIdx > 0
                        ? () => setTraceTask(shown[traceIdx - 1])
                        : undefined
                    }
                    onNext={
                      traceIdx > -1 && traceIdx < shown.length - 1
                        ? () => setTraceTask(shown[traceIdx + 1])
                        : undefined
                    }
                  />
                )}
              </SideDrawer>
              <Drawer
                anchor="right"
                open={!!openTask}
                onClose={closeTask}
                ModalProps={{
                  BackdropProps: { style: { backgroundColor: "transparent" } },
                }}
                sx={{
                  ...coveredDrawerSx(callCovered),
                  zIndex: (t) => t.zIndex.modal,
                  "&& .MuiDrawer-paper": {
                    width: "auto",
                    maxWidth: "100vw",
                    bgcolor: "background.paper",
                    backgroundImage: "none",
                    overflow: "hidden",
                  },
                }}
              >
                {openTask && tab === "cost" && (
                  <CostStrip task={openTask} env={env} envState={envState} />
                )}
                {openTask && (
                  /* The real drawer fills what's left under the cost strip
                     (on its own it takes the whole window height). */
                  <Box
                    sx={{
                      flex: 1,
                      minHeight: 0,
                      "& > :first-of-type": { height: "100%", minHeight: 0 },
                    }}
                  >
                    <VoiceDetailDrawerV2
                      key={`${openTask.id}-${evalFocus || ""}`}
                      /* The verdict and its cause (§4.10.10) ride in the real
                     drawer's Status chip — "Failed · agent". */
                      data={{
                        /* A multi-agent app: its handoffs, as their own tab. */
                        simApprovals: approvalOf({
                          env,
                          envState,
                          task: openTask,
                        }) ? (
                          <ApprovalTimeline env={env} task={openTask} />
                        ) : null,
                        /* Artifacts recorded or not, a failed check's source
                           and dispute notes. */
                        /* A platform agent: the events its platform sent for this call. */
                        simEvents: envState?.agent?.values?.provider ? (
                          <ProviderEvents
                            task={openTask}
                            provider={envState.agent.values.provider}
                          />
                        ) : null,
                        simEvidence: (
                          <Stack spacing={1.5} sx={{ pb: 2 }}>
                            <DuplicateWriteNote
                              task={openTask}
                              sx={{ mx: 2, mt: 1.5 }}
                            />
                            <SharedState
                              agent={envState?.agent}
                              task={openTask}
                              sx={{ mx: 2 }}
                            />
                            <TrialArtifacts
                              items={trialArtifactsOf({
                                task: openTask,
                                env,
                                envState,
                                stage: "voice",
                                conversational: true,
                                voice: true,
                              })}
                              onOpen={() => {}}
                              hideOpen
                            />
                            {["failed", "flaky", "unmeasured"].includes(
                              openTask.status,
                            ) && (
                              <Stack spacing={1} sx={{ px: 2 }}>
                                <CheckSource
                                  task={openTask}
                                  env={env}
                                  envState={envState}
                                  run={
                                    (envState.runs || []).find(
                                      (r) => r.id === runId,
                                    ) || null
                                  }
                                />
                                <DisputeNotes
                                  envState={envState}
                                  patch={patch}
                                  runId={runId}
                                  task={openTask}
                                  score={
                                    openTask.repeats > 1
                                      ? `${openTask.passes}/${openTask.repeats}`
                                      : openTask.status
                                  }
                                />
                              </Stack>
                            )}
                          </Stack>
                        ),
                        simHandoffs:
                          membersOf(envState?.agent).length > 1 ? (
                            <HandoffTimeline
                              agent={envState?.agent}
                              task={openTask}
                            />
                          ) : null,
                        ...taskToVoiceData(openTask, {
                          env,
                          voice: true,
                          provider: envState?.agent?.values?.provider || null,
                          textRehearsal: isTextRehearsal(env, envState?.agent),
                          /* The Graph tab draws the scenario's path and where the call left it. */
                          scenario:
                            (envState.scenarios || []).find(
                              (sc) => sc.id === openTask.id,
                            ) || null,
                          failedGoal:
                            subGoalResultsOf(
                              openTask,
                              env,
                              envState,
                            ).items.find(
                              (g) =>
                                g.status === "failed" && g.mark === "critical",
                            )?.text || null,
                        }),
                        ...(verdicts?.get(openTask.id) && {
                          status: verdicts.get(openTask.id).label,
                        }),
                      }}
                      initialTab={evalFocus ? "evaluations" : undefined}
                      focusEvalName={evalFocus || undefined}
                      hideEvalRowFix
                      onClose={closeTask}
                      onPrev={goPrev}
                      onNext={goNext}
                      hasPrev={!!goPrev}
                      hasNext={!!goNext}
                      hideAnnotationTab
                    />
                  </Box>
                )}
              </Drawer>
            </>
          );
        }

        return (
          <SideDrawer
            open={!!openTask}
            onClose={closeTask}
            width={{ xs: "100%", md: 960 }}
          >
            {openTask && (
              <CallDrawer
                showCost={tab === "cost"}
                key={`${openTask.id}-${focusStep || ""}-${evalFocus || ""}-${openPane || ""}`}
                task={openTask}
                focus={focusStep || undefined}
                initialPane={openPane || undefined}
                focusEvalName={evalFocus || undefined}
                env={env}
                envState={envState}
                patch={patch}
                addAgentVersion={addAgentVersion}
                runId={runId}
                verdict={verdicts?.get(openTask.id) || null}
                onClose={closeTask}
                onPrev={goPrev}
                onNext={goNext}
              />
            )}
          </SideDrawer>
        );
      })()}
    </Box>
  );
}

RunResults.propTypes = {
  env: PropTypes.object,
  runId: PropTypes.string,
  tasks: PropTypes.array,
  stats: PropTypes.object,
  evals: PropTypes.array,
};

/*
  The two re-run modes (PRD v7 §4.10.8, closes Q29). "Re-run exact snapshot"
  is the default for comparisons: the run's own scenario versions,
  environment version, pins and seed — only the agent version may change, so
  any difference is the agent's. "Run as configured now" picks up edits.
*/
function RerunModes({ run, canSnapshot, envLabel, agents = [], onRun }) {
  const [agent, setAgent] = useState(run?.agentVersion || null);
  const itemSx = {
    alignItems: "flex-start",
    gap: 0.75,
    py: 1.25,
    whiteSpace: "normal",
  };
  const textProps = {
    primaryTypographyProps: { typography: "s2", fontWeight: 600 },
    secondaryTypographyProps: {
      typography: "s3",
      color: "text.tertiary",
      sx: { whiteSpace: "normal" },
    },
  };
  return (
    <>
      {canSnapshot && (
        <>
          <MenuItem onClick={() => onRun("snapshot", agent)} sx={itemSx}>
            <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
              <Iconify
                icon="solar:history-linear"
                width={16}
                sx={{ color: "text.primary" }}
              />
            </ListItemIcon>
            <ListItemText
              primary={
                <>
                  Re-run exact snapshot
                  <Box
                    component="span"
                    sx={{
                      ml: 0.75,
                      typography: "s3",
                    }}
                  >
                    default for comparisons
                  </Box>
                </>
              }
              secondary={`Same scenario versions, environment ${envLabel || "version"}, pins and seed${run?.repeats ? `, ${run.repeats} repeats` : ""}. Only the agent version can change.`}
              {...textProps}
            />
          </MenuItem>
          {agents.length > 1 && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{
                px: 2,
                pb: 1.25,
                pl: "44px",
                flexWrap: "wrap",
                rowGap: 0.5,
              }}
            >
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Agent
              </Typography>
              {agents.map((a) => (
                <Box
                  key={a}
                  role="button"
                  tabIndex={0}
                  onClick={() => setAgent(a)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") setAgent(a);
                  }}
                  sx={{
                    px: 0.75,
                    height: 22,
                    display: "inline-flex",
                    alignItems: "center",
                    borderRadius: "4px",
                    cursor: "pointer",
                    typography: "s3",
                    fontWeight: 600,
                    border: "1px solid",
                    borderColor: agent === a ? "text.primary" : "divider",
                    color: agent === a ? "text.primary" : "text.secondary",
                  }}
                >
                  {a}
                  {a === run?.agentVersion && (
                    <Box
                      component="span"
                      sx={{ ml: 0.5, color: "text.tertiary", fontWeight: 400 }}
                    >
                      this run
                    </Box>
                  )}
                </Box>
              ))}
            </Stack>
          )}
        </>
      )}
      <MenuItem onClick={() => onRun("now", null)} sx={itemSx}>
        <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
          <Iconify
            icon="solar:refresh-linear"
            width={16}
            sx={{ color: "text.primary" }}
          />
        </ListItemIcon>
        <ListItemText
          primary="Run as configured now"
          secondary="Picks up every edit since, with a new seed. Compares only the pairs that still match."
          {...textProps}
        />
      </MenuItem>
    </>
  );
}
RerunModes.propTypes = {
  run: PropTypes.object,
  canSnapshot: PropTypes.bool,
  envLabel: PropTypes.string,
  agents: PropTypes.array,
  onRun: PropTypes.func,
};

/* Map a task's raw status to one of the chip buckets. An errored episode is a
   measured failure (it reached a "this went wrong" verdict) — grouped with
   Failing, not Inconclusive, so the chips agree with the summary strip, the
   Analytics tab, and the failure groupings (which do bucket errored rows).
   Only genuinely unmeasured episodes are inconclusive. */
function statusBucket(t) {
  if (t.status === "passed") return "passing";
  if (t.status === "flaky") return "mixed";
  if (t.status === "unmeasured") {
    /* Inconclusive, test invalid or error — none counts for or against the agent. */
    const o = outcomeOfCause(causeOf(t));
    return o === "test-invalid"
      ? "invalid"
      : o === "error"
        ? "error"
        : "inconclusive";
  }
  return "failing";
}

/* The chips for outcomes that count neither for nor against the agent. */
const NOT_COUNTED_CHIPS = ["inconclusive", "invalid", "error"];

/* One name per status, the same on the chip and on the row. `about` says
   what each means — Flaky only exists because scenarios are repeated. */
const STATUS_CHIPS = [
  { id: "all", label: "All", role: null, about: "" },
  {
    id: "failing",
    label: "Failing",
    role: "danger",
    about: "Failed every repeat, or errored.",
  },
  {
    id: "mixed",
    label: "Flaky",
    role: "warning",
    about:
      "Passed some repeats and failed others — the agent isn't reliable here.",
  },
  {
    id: "inconclusive",
    label: "Unmeasured",
    role: null,
    about:
      "Not enough evidence to grade. Never counted as a pass; left out of pass rates.",
  },
  {
    id: "invalid",
    label: "Test invalid",
    role: null,
    about: "The test broke, not the agent. Counts neither for nor against it.",
  },
  {
    id: "error",
    label: "Error",
    role: null,
    about:
      "Our infrastructure or the connection failed. Counts neither for nor against the agent.",
  },
  {
    id: "passing",
    label: "Passing",
    role: "success",
    about: "Passed every repeat.",
  },
];

/* Outcome quick-filter — one chip per status bucket plus All. The active chip
   is filled in its status colour; the rest are quiet outlines. Each carries
   its count so the distribution is legible before you click. */
function StatusFilterChips({ value, counts, onChange, blocked = [] }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ flexWrap: "wrap", rowGap: 0.75 }}
    >
      {/* An outcome nothing ended in isn't a filter worth offering — hidden,
          unless it's the one selected. Passing and failing always show. */}
      {STATUS_CHIPS.filter(
        (chip) =>
          ["all", "failing", "passing"].includes(chip.id) ||
          (counts[chip.id] ?? 0) > 0 ||
          value === chip.id,
      ).map((chip) => {
        const active = value === chip.id;
        const dot = chip.role ? `status.${chip.role}.solid` : "text.disabled";
        const accent = chip.role ? `status.${chip.role}.solid` : "text.primary";
        const count = counts[chip.id] ?? 0;
        /* Disabled when empty, or when the current failure-only grouping has
           no bucket for this outcome (passing / inconclusive). */
        const blockedByGrouping = blocked.includes(chip.id);
        const disabled =
          (chip.id !== "all" && count === 0) || blockedByGrouping;
        return (
          <Tooltip
            key={chip.id}
            arrow
            title={
              blockedByGrouping
                ? "Not available while grouping by a failure view — passing runs have no failure bucket"
                : chip.about
            }
          >
            <Box
              role="button"
              tabIndex={disabled ? -1 : 0}
              onClick={() => !disabled && onChange(chip.id)}
              onKeyDown={(e) => {
                if (!disabled && (e.key === "Enter" || e.key === " "))
                  onChange(chip.id);
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.625,
                height: 28,
                px: 1.125,
                borderRadius: 1,
                border: "1px solid",
                borderColor: active ? accent : "divider",
                bgcolor: active
                  ? chip.role
                    ? `status.${chip.role}.bg`
                    : "action.selected"
                  : "transparent",
                color: disabled ? "text.disabled" : "text.primary",
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.5 : 1,
                transition: "border-color 120ms, background-color 120ms",
                "&:hover": disabled
                  ? {}
                  : { borderColor: active ? accent : "text.disabled" },
              }}
            >
              {chip.id !== "all" && (
                <Box
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    bgcolor: dot,
                    flexShrink: 0,
                  }}
                />
              )}
              <Typography
                sx={{ typography: "s2", fontWeight: 600, whiteSpace: "nowrap" }}
              >
                {chip.label}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                  color: active ? "inherit" : "text.tertiary",
                }}
              >
                {count}
              </Typography>
            </Box>
          </Tooltip>
        );
      })}
    </Stack>
  );
}
StatusFilterChips.propTypes = {
  value: PropTypes.string,
  counts: PropTypes.object,
  onChange: PropTypes.func,
  blocked: PropTypes.array,
};

/* "finished 2m ago" — relative for anything recent, an absolute date once it's
   more than a week old. Tries the timestamp fields a run might carry and
   returns null on a missing/unparseable date so the caller can omit it. */
function runFinishedLabel(run) {
  const raw = run?.finishedAt || run?.completedAt || run?.createdAt;
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 45) return "just now";
  if (s < 90) return "a minute ago";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 604800) return `${Math.round(s / 86400)}d ago`;
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}
