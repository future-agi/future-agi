import PropTypes from "prop-types";
import { atOnceOf } from "./atOnce";
import { comparisonOf } from "../_mock/pairs";
import { snapshotLabelOf } from "../_mock/envSnapshot";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSnackbar } from "notistack";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
  Link,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import RunResults from "./RunResults";
import {
  publishRun,
  installMockExecutionAdapter,
} from "../_mock/executionAdapter";
import { rebuildRun, TRIAL_CHANGES } from "../_mock/comparison";
import {
  agentVersions,
  currentAgentVersion,
  currentEnvVersion,
  environmentVersions,
} from "../_mock/versions";
import { runInputs, toolFit } from "../_mock/toolFit";
import { runIdentityOf, isCandidateRun } from "../_mock/runIdentity";
import { runSummaries } from "../_mock/comparison";
import { runReportOf } from "../_mock/runReport";
import { digestDestinationsOf } from "../_mock/runDigest";
import SharedReport from "./SharedReport";
import ClarifyCard from "./ClarifyCard";
import SpendCard from "./SpendCard";
import {
  RATE_PLAN,
  WORKSPACE_POLICY,
  allowanceOf,
  projectOf,
  creditsOfTask,
  estimateRunPlan,
  judgeCreditsOf,
} from "../_mock/pricing";
import { deriveUseCaseLabel } from "./traceTableHelpers";
import { runPlan } from "../_mock/proofs";
import { runReadiness } from "../_mock/setupGaps";
import { twinTimelineFor } from "../_mock/twins";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { getEval, resolveEval } from "../_mock/evals";
import { parallelOf, runSizeOf, envTurnLimitOf } from "../_mock/runStream";
import { runDefaultsOf } from "../_mock/rsiModel";
import { staleScenariosOf } from "../_mock/runCauses";
import { runProofKindOf, gradingOf } from "../_mock/runRollup";
import { subGoalBasisOf } from "../_mock/scenarioSubGoals";
import { OUTCOMES, causeOf, outcomeOfCause } from "../_mock/decoupled";
import { ProofKindChip } from "./RunSummaryChip";
import { SHADOW_SUMMARY } from "../_mock/sandbox";
import { postSlack, getIntegrations } from "../_mock/integrations";
import { useSimStore, useEnvState } from "../store";
import {
  StatusDot,
  StatusChip,
  ScorePill,
  EmptyState,
  pulse,
} from "../components/primitives";
import useRunPlayer from "./useRunPlayer";
import SecretCheckDialog from "./SecretCheckDialog";
import FixRunNote from "../errorFeed/FixRunNote";
import { improvementPath } from "../improvements/improvementNav";
import IssueReturnBanner from "../errorFeed/IssueReturnBanner";
import { failedSecretChecksOf } from "../_mock/envConfig";
import Stage from "./stages";
import FromImprovement from "./FromImprovement";
import { candidateSourceOf } from "../_mock/candidateSource";
import RunGroupBar from "./RunGroupBar";
import { groupMembersOf, settledRun } from "../_mock/runGroup";
import { gradedRuleIds } from "../_mock/provenance";
import { subTasksFor } from "../_mock/contract";
import { pinsForNewRun } from "../_mock/envModel";
import { pinnedScenarios } from "../_mock/decoupled";
import { withRegrade } from "../_mock/regrade";
import { envLabelOf } from "../_mock/envVersionKinds";
import { milestoneOf } from "../_mock/runScripts";
import { currentUser } from "../_mock/scenarioProvenance";
import { monthUsage } from "../_mock/usageLedger";
import { clickable, focusRing } from "../components/clickable";
import { scenarioTitle } from "../_mock/scenarios";
import { approvalOf } from "../_mock/approvals";
import { actorsInRun, matchesTrialFilters } from "../_mock/trialFilters";
import TrialFilters from "./TrialFilters";

// Flaky counts and results read as neutral words — amber never colours text.
const FLAKY = "text.primary";
const UNMEASURED = "text.tertiary";
const RED = "status.danger.fg";

/**
 * The live run.
 *
 * Three columns: what is queued, what is happening right now, and what the
 * graders think of it. The middle column is the point of the whole screen —
 * a user should be able to leave this open on a second monitor and understand
 * their agent's behaviour without reading a single log line.
 *
 * Keyed on the run, so going from one run to another — a trial's "From
 * Improvement 2" back to the run it started from — opens that run fresh
 * rather than carrying the last one's drawer, tab and filters across.
 */
export default function LiveRunView() {
  const { runId } = useParams();
  const [params] = useSearchParams();
  /* `?shared=<token>` — the read-only report a share link opens. */
  if (params.get("shared")) return <SharedReport key={runId} />;
  return <LiveRun key={runId} />;
}

function LiveRun() {
  const { envId, runId } = useParams();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { state, commit } = useSimStore();
  const { envState, recordRun, patch } = useEnvState(envId);

  /*
    A run-group member that hasn't streamed yet. A group records every member
    the moment it starts, so opening one finds a record — but it is a run to
    play, not one to read back. Latched: playing it rewrites the record, and
    the pairing it pinned must not change under the player mid-run.
  */
  const recorded = envState.runs.find((r) => r.id === runId);
  const pendingRef = useRef(null);
  if (!pendingRef.current && recorded?.pending && !recorded.finishedAt)
    pendingRef.current = recorded;
  const pendingMember = pendingRef.current;
  /* A fix's first run — said in the notes row, never as a banner. */
  const fixRun = ["fix", "issue"].includes(
    (recorded || pendingMember)?.measuredFor?.kind,
  );
  /* Its first run never waits on a person — a question leaves that scenario unscored. */
  const fixFirstRun = (recorded || pendingMember)?.measuredFor?.kind === "fix";

  const env =
    getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);
  const surface = getSurface(env?.surface);

  const evals = useMemo(
    () => envState.evals.map(resolveEval).filter(Boolean),
    [envState.evals],
  );

  /*
    A run is not always a run of everything. `?only=` carries the subset a
    comparison sent here — re-run these four blockers against the new version —
    and the run records what it actually covered, so nothing downstream reads a
    four-scenario check as a full sweep that lost twenty-eight rows.
  */
  /*
    `?snapshot=<run>` — "Re-run exact snapshot" (PRD v7 §4.10.8): replay the
    scenarios, environment version, pins and seed the source run used. The
    agent version is the only thing that may differ (`?agent=`). Without it,
    a run is "as configured now" and picks up every edit.
  */
  const snapshotParam = params.get("snapshot");
  const snapshotSrc = snapshotParam
    ? (envState.runs || []).find(
        (r) => r.id === snapshotParam && r.finishedAt,
      ) || null
    : null;
  /*
    `?live=1` on an improvement's run (OPT-…-tN): watch that candidate play,
    replayed from the run it started from with each scenario landing where
    the candidate's own result says. A replay — it records nothing, sends no
    alerts, and the improvement's numbers stay the search's.
  */
  const watchTrial = useMemo(() => {
    if (params.get("live") !== "1") return null;
    const m = /^(OPT-\d+)-t(\d+)$/.exec(runId || "");
    if (!m) return null;
    const opt = (envState.optimizations || []).find((o) => o.id === m[1]);
    const trial = (opt?.result?.trials || []).find((t) => t.n === Number(m[2]));
    const sourceRun = (envState.runs || []).find(
      (r) => r.id === opt?.fromRunId,
    );
    return trial && sourceRun ? { opt, trial, sourceRun } : null;
    /* Re-read once the store has loaded; a replay writes nothing, so these
       don't change under it. */
  }, [params, runId, envState.optimizations, envState.runs]);
  /* A candidate is tried on the target scenarios — what its replay plays. */
  const watchIds = watchTrial
    ? (watchTrial.opt.result?.train || [])
        .map((t) => t.scenarioId || t.id)
        .filter(Boolean)
    : [];
  const only =
    params.get("only") ||
    (watchIds.length ? watchIds.join(",") : null) ||
    (watchTrial?.sourceRun?.scenarioIds?.length
      ? watchTrial.sourceRun.scenarioIds.join(",")
      : null) ||
    (snapshotSrc?.scenarioIds?.length
      ? snapshotSrc.scenarioIds.join(",")
      : null);
  /* `?agent=` runs a specific agent version — how a comparison re-runs v1 on
     the environment v2 was rebuilt for. Otherwise the current one. */
  /* Once started, the run keeps the version it recorded — production moving
     mid-run doesn't change what this run tested. */
  const agentParam =
    params.get("agent") ||
    recorded?.agentVersion ||
    pendingMember?.agentVersion ||
    snapshotSrc?.agentVersion;
  const runAgent =
    agentParam && agentVersions(envState).some((v) => v.label === agentParam)
      ? agentParam
      : currentAgentVersion(envState)?.label;
  /* `?candidate=sha:<sha>` or `?candidate=upload:<archive>` — the candidate
     taken from a code snapshot (a commit, or an uploaded archive) instead of
     an agent version; the run says "from snapshot <sha>". */
  const candidate = candidateSourceOf(
    params.get("candidate") || recorded?.candidateSource?.param,
    envState.agent,
  );
  /* `?env=` runs on a specific environment version without moving the
     environment's own pin — how Compare re-runs an older agent on the newer
     world. Otherwise the pinned one. */
  const envParam =
    params.get("env") || pendingMember?.envVersion || snapshotSrc?.envVersion;
  const runEnv =
    envParam &&
    environmentVersions(env, envState).some((v) => v.label === envParam)
      ? envParam
      : currentEnvVersion(env, envState)?.label;
  /* Shown as the environment version (a snapshot of world, scenarios, evals
     and caller); the stored world label is what's matched and recorded. */
  const runEnvShown = envLabelOf(env, envState, runEnv);
  /* Only the scenarios that exist in this environment version, re-proved
     against it at run start — what still can't stage is skipped and listed.
     A selection is never widened to the whole set. */
  /* A fix's first run also plays the issue's scenarios it carries — the
     fix's own, never written into the environment. */
  const fixScenarios =
    pendingMember?.fixScenarios || watchTrial?.sourceRun?.fixScenarios || null;
  const plan = useMemo(
    () =>
      runPlan(
        env,
        fixScenarios
          ? {
              ...envState,
              scenarios: [...envState.scenarios, ...fixScenarios],
            }
          : envState,
        {
          only: only ? only.split(",") : null,
          envVersion: runEnv,
        },
      ),
    /* Keyed on the scenario list, not the whole env state — recording the run
       itself updates the env state, and must not rebuild the run mid-flight. */
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [only, envState.scenarios, runEnv, fixScenarios],
  );
  const { run: planned, skipped, poolSize } = plan;
  /* An exact snapshot plays each scenario at the version its source pinned —
     the text it had then, not today's edit (§4.10.8). As configured now, today's. */
  const scenarios = useMemo(() => {
    const rows = snapshotSrc ? pinnedScenarios(planned, snapshotSrc) : planned;
    if (!watchTrial || !env) return rows;
    /* Watching a candidate: each scenario lands where the candidate's own
       result says — the trial's change, else how it went on its source run. */
    const { trial, sourceRun } = watchTrial;
    const reps = sourceRun.repeats || 1;
    const before = new Map(
      rebuildRun(env, envState, sourceRun).map((t) => [
        t.scenarioId || t.id,
        t.status,
      ]),
    );
    const per = trial.perScenario || {};
    return rows.map((sc) => {
      const changed = TRIAL_CHANGES[per[sc.id]];
      const status = changed || before.get(sc.id);
      const samples =
        status === "passed"
          ? Array(reps).fill("passed")
          : status === "failed"
            ? Array(reps).fill("failed")
            : status === "flaky"
              ? Array.from({ length: reps }, (_, i) =>
                  i % 2 ? "passed" : "failed",
                )
              : null;
      /* Counted in a candidate's run — the issue's scenarios are its targets. */
      const { fixScenario: _x, fromIssue: _f, ...row } = sc;
      return samples ? { ...row, forcedSamples: samples } : row;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planned, snapshotSrc?.id, watchTrial, env]);
  /* A re-run of some scenarios, not the whole set. */
  const partial = !!only && scenarios.length + skipped.length < poolSize;

  /*
    Trials per scenario (PRD §10.2 AC-10.7 — reliability across
    repeated trials). The Scenarios SelectionBar sets it via the
    `?trials=` param; when absent, we keep the historical 3, the
    smallest n where "passed twice, failed once" is sayable.
    Clamped 1–20 so a bad URL can't blow up the batch.
  */
  const repeats = (() => {
    const raw = Number(
      params.get("trials") ||
        pendingMember?.repeats ||
        watchTrial?.sourceRun?.repeats ||
        snapshotSrc?.repeats,
    );
    /* No count asked for: the environment's default (3 unless Settings changed it). */
    if (!Number.isFinite(raw) || raw < 1)
      return runDefaultsOf(envState).repeats;
    return Math.min(20, Math.floor(raw));
  })();

  /* What the outcome depends on — the agent version and the environment
     version this run pins — read exactly as a later replay reads them.
     Memoised: the player rebuilds the run whenever its inputs change identity. */
  /* What a run replays its pins from: itself once recorded, else the
     snapshot it re-runs, else nothing (a new run takes today's). */
  const pinSource = recorded && !recorded.pending ? recorded : snapshotSrc;
  const inputs = useMemo(
    () =>
      runInputs(env, envState, {
        agent: runAgent,
        envVersion: runEnv,
        /* A finished run replays the faults it pinned, not today's dial. */
        toolFaults: pinSource ? pinSource.toolFaults || null : undefined,
        actorEdits: pinSource ? pinSource.actorEdits || null : undefined,
        actors: snapshotSrc ? snapshotSrc.actors : undefined,
        admissionGates: pinSource ? pinSource.admissionGates || 1 : 3,
        /* The updated RSI PRD's run rules — retry once, pull a scenario test
         invalid twice, required evaluations: on for every new run; a stored
         run (or the snapshot it re-runs) replays what it pinned. */
        outcomes: pinSource ? pinSource.outcomes || 0 : 1,
        requiredEvals: pinSource ? pinSource.requiredEvals || 0 : 1,
        /* Sub-goals decide, and the turn limit at the environment's limit:
         on for every new run; a stored run replays what it pinned. */
        gradingRule: pinSource ? pinSource.gradingRule || 0 : 2,
        turnLimits: pinSource ? pinSource.turnLimits || 0 : 1,
        turnLimit: pinSource ? pinSource.turnLimit : envTurnLimitOf(envState),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      env,
      runAgent,
      runEnv,
      envState.envVersions,
      envState.agentVersions,
      envState.actors,
      envState.toolEnv,
      recorded?.id,
      recorded?.pending,
      snapshotSrc?.id,
    ],
  );
  /* Which rule grades this run and what its sub-goals are checked against —
     a new run takes the sub-goal rule; a stored one (or its snapshot) keeps
     its own. */
  const liveGrading = gradingOf(pinSource || null, envState, {
    fresh: !pinSource,
  });
  /* The snapshot's seed, so the same scenarios meet the same draws. */
  const replaySeed = snapshotSrc
    ? snapshotSrc.replaySeed || snapshotSrc.id
    : null;
  /* …and the graders it was scored by. */
  const runEvals = useMemo(
    () =>
      snapshotSrc?.evals
        ? snapshotSrc.evals.map(resolveEval).filter(Boolean)
        : evals,
    [snapshotSrc, evals],
  );

  /* Outdated / Re-check scenarios as this run starts: they run, but count
     toward no decision. Pinned on the record; a stored run replays its own. */
  const liveStale = useMemo(
    () =>
      pinSource || !env
        ? null
        : staleScenariosOf(env, envState, scenarios, runAgent || null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scenarios, pinSource?.id],
  );
  const staleIds = pinSource ? pinSource.staleScenarios || null : liveStale;
  /* Up to 4 conversations at once where parallel runs are on. */
  const liveParallel = parallelOf(envState);
  /* Over 200 conversations, the run takes the environment's full
     "Conversations at once" (Settings) instead of the live cap — the same
     run, the same page, just more at a time. */
  const overCap = runSizeOf({
    scenarios: scenarios.length,
    repeats,
    surface: env?.surface,
    parallel: liveParallel,
  }).over;
  const parallel = overCap ? atOnceOf(envState) : liveParallel;
  /* 1–20 repeats, at most 200 conversations, and — for voice — how long it takes. */
  const size = runSizeOf({
    scenarios: scenarios.length,
    repeats,
    surface: env?.surface,
    parallel,
  });
  /* The voice estimate is quoted for at most 200 calls; over that, say the
     whole run at the number it really takes at once. */
  const voiceLine =
    size.voice && overCap
      ? (() => {
          const mins = (m) =>
            m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
          const serial = Math.round(size.conversations * 2.4);
          const atOnce = Math.ceil(serial / parallel / 5) * 5;
          return `≈ ${mins(serial)} at 1 call at a time · ≈ ${mins(atOnce)} at ${parallel}`;
        })()
      : size.voice?.line;

  /* The run plan's spending limit — on the link it started from, or on the
     record a run group wrote for it. */
  /* This month's usage when the run opened — read once, not on every
     state change while it plays. */
  const [monthAtStart] = useState(() => monthUsage(state));
  /* A group member's share of the group's limit, when it has one. */
  const groupCap =
    recorded?.budget?.groupCap || pendingMember?.budget?.groupCap || null;
  const runBudget = (() => {
    const cap =
      Number(params.get("cap")) ||
      recorded?.budget?.cap ||
      pendingMember?.budget?.cap ||
      null;
    if (!cap) return null;
    const alertAt =
      (Number(params.get("warn")) ||
        Math.round((recorded?.budget?.alertAt || 0.8) * 100)) / 100;
    const voiceRun = ["voice", "multi"].includes(env?.surface);
    const judgeCredits = judgeCreditsOf(runEvals);
    /* What's left this month — workspace, project and the person's own
       limit. Running low holds queued work before another call starts.
       ?credits=low shows it. */
    const month = monthAtStart;
    const me = currentUser().name;
    /* Other runs still playing hold their limits too. */
    const held = month.held || { total: 0, byProject: {}, byUser: {} };
    const p = projectOf(env);
    const lefts = [
      allowanceOf(month.total + held.total).left,
      WORKSPACE_POLICY.projects?.[p]?.limit != null
        ? WORKSPACE_POLICY.projects[p].limit -
          (month.byProject[p] || 0) -
          (held.byProject[p] || 0)
        : Infinity,
      WORKSPACE_POLICY.users?.[me]?.limit != null
        ? WORKSPACE_POLICY.users[me].limit -
          (month.byUser[me] || 0) -
          (held.byUser[me] || 0)
        : Infinity,
    ];
    const accountLeft =
      params.get("credits") === "low"
        ? Math.round(cap * 0.4)
        : Math.min(...lefts);
    /* ?provider=limit: the voice platform's own account runs out after a
       few calls — its limit, not ours, stops new ones. */
    const provider = envState?.agent?.values?.provider;
    const providerCalls =
      params.get("provider") === "limit" && voiceRun && provider ? 3 : null;
    return {
      cap,
      alertAt,
      accountLeft,
      providerCalls,
      providerName:
        { vapi: "Vapi", retell: "Retell", livekit: "LiveKit", bland: "Bland" }[
          provider
        ] || "voice platform",
      voice: voiceRun,
      creditsOf: (task, { high = false } = {}) =>
        creditsOfTask(
          task,
          { voice: voiceRun, judgeCredits, repeats },
          { high },
        ),
    };
  })();
  const playerArgs = {
    /* A replay never stops to ask — the candidate's results are already in. */
    ...(watchTrial && { clarify: false }),
    ...(!watchTrial && fixFirstRun && { clarify: "skip" }),
    budget: runBudget,
    seed: replaySeed || runId,
    scenarios,
    stage: surface.stage,
    evals: runEvals,
    repeats,
    concurrency: parallel,
    staleIds,
    ...inputs,
  };
  /* Once the run starts, it plays what it started with. Recording the run
     re-reads its pins from the record — equal values, new objects — and the
     player would take that as a new run and boot it a second time. */
  const startedWith = useRef(null);
  if (startedWith.current && startedWith.current.runId !== runId)
    startedWith.current = null;
  const player = useRunPlayer(startedWith.current?.args || playerArgs);

  const {
    phase,
    start,
    finishNow,
    tasks,
    focus,
    focusId,
    setFocusId,
    stats,
    elapsed,
    sinceLastEvent,
    stalled,
    blocked,
    answer,
    clarifications,
    spend,
    dismissAlert,
    setCap,
    stopAtLimit,
    notStartedIds,
  } = player;
  /* Declared before the effect that records the run, so the freeze lands
     first. */
  useEffect(() => {
    if (phase === "running" && !startedWith.current)
      startedWith.current = { runId, args: playerArgs };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  /* A secret that failed its access check, said before the run starts. */
  const failedSecrets = useMemo(
    () => failedSecretChecksOf(envState),
    [envState],
  );
  const [secretAsk, setSecretAsk] = useState(false);
  const [secretsOk, setSecretsOk] = useState(false);
  /* The scenario list's filters — scenario, status, failure domain, actor. */
  const [trialFilter, setTrialFilter] = useState({});

  /*
    A scenario paused on a clarifying question (AC-15.13). Long runs are left
    unattended, so the question comes to the person: an in-app notice, and a
    browser notification if they've already allowed them — never a permission
    prompt out of nowhere. The inline card below is where it's answered.
  */
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  useEffect(() => {
    if (!blocked) return;
    const sc = tasks.find((t) => t.id === blocked.taskId);
    const name = sc?.name || sc?.title || "A scenario";
    const n = envState.runs.find((r) => r.id === runId)?.ordinal;
    const title = `${n ? `Run ${n}` : "Your run"} needs an answer`;
    enqueueSnackbar(`${title} — ${name}: ${blocked.question}`, {
      variant: "info",
      autoHideDuration: 9000,
      action: (key) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            setFocusId(blocked.taskId);
            closeSnackbar(key);
          }}
        >
          Answer
        </Button>
      ),
    });
    try {
      if (
        typeof window !== "undefined" &&
        "Notification" in window &&
        window.Notification.permission === "granted"
      ) {
        const note = new window.Notification(title, {
          body: `${name}: ${blocked.question}`,
          tag: `clarify-${runId}`,
        });
        note.onclick = () => {
          window.focus();
          setFocusId(blocked.taskId);
          note.close();
        };
      }
    } catch {
      /* notifications unavailable */
    }
  }, [blocked?.askedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  /*
    A run that is already in history is being read, not run. Replaying it —
    provisioning animation and all — was the old behaviour and it is wrong
    twice over: it wastes the reader's time, and it invites the suspicion that
    the numbers are generated fresh each time rather than being that run's.

    The latch matters because a run finishing puts itself into history: without
    it, the live view would flip to read-only in the same commit that finished
    it.
  */
  /*
    Trial rehydration.

    A trial id is `${OPT-XXXXX}-t${n}` — a self improvement's trial, which
    is a real simulation run (the search executed the candidate against
    this environment). If the URL points at one, look it up in the
    environment's optimizations rather than expecting a matching row in
    envState.runs (trials are not stored there — they are derived).
  */
  const trialMatch = useMemo(() => {
    const m = /^(OPT-\d+)-t(\d+)$/.exec(runId || "");
    if (!m) return null;
    const opt = (envState.optimizations || []).find((o) => o.id === m[1]);
    if (!opt) return null;
    const trial = (opt.result?.trials || []).find((t) => t.n === Number(m[2]));
    if (!trial) return null;
    const sourceRun = (envState.runs || []).find((r) => r.id === opt.fromRunId);
    return { opt, trial, sourceRun };
  }, [runId, envState.optimizations, envState.runs]);

  const stored =
    trialMatch && !watchTrial
      ? /* Synthesize a stored-like record pointing at the source run's seed so
       rebuildRun can produce a task list. The trial's per-scenario outcomes
       are folded in below. */
        {
          id: trialMatch.sourceRun?.id || runId,
          agentVersion: trialMatch.sourceRun?.agentVersion || "trial",
          envVersion: trialMatch.sourceRun?.envVersion,
          scenarioIds:
            trialMatch.sourceRun?.scenarioIds ||
            Object.keys(trialMatch.trial.perScenario || {}),
          repeats: trialMatch.sourceRun?.repeats || 1,
        }
      : pendingMember
        ? undefined
        : recorded;

  /* Persist the start-of-run re-proof, so the Scenarios tab reads the same
     verdicts this run used. Only for a new run — history is read, not re-run. */
  const isNewRun = !trialMatch && !stored;
  useEffect(() => {
    if (!isNewRun || !env) return;
    /* Only the environment's own rows — a fix's scenarios stay the fix's. */
    const fixIds = new Set((fixScenarios || []).map((sc) => sc.id));
    const reproofed = plan.reproofed.filter((sc) => !fixIds.has(sc.id));
    if (reproofed.some((sc, i) => sc !== envState.scenarios[i]))
      patch({ scenarios: reproofed });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, isNewRun]);
  /* Every way into a run ends here, so this is the gate that can't be skipped. */
  const readiness = runReadiness(envState, env);

  const wasLive = useRef(false);
  useEffect(() => {
    if (phase === "running") wasLive.current = true;
  }, [phase]);
  /* Trials are always read-only: the search already ran them. */
  const readOnly =
    (!!trialMatch && !watchTrial) || (!wasLive.current && !!stored);

  const storedTasks = useMemo(() => {
    if (!readOnly || !env || !stored) return [];
    /* Read under the graders it was re-graded with, if it was (AC-12.23). */
    const base = withRegrade(rebuildRun(env, envState, stored), stored);
    if (!trialMatch) return base;
    /*
      Overlay the trial's per-scenario verdicts. The source run's traces are
      what a trial's run would have looked like; the trial's outcomes are
      what actually happened. Everything a reader clicks — Traces, Analytics,
      Verify — reads a run whose numbers match the trial score.
    */
    const per = trialMatch.trial.perScenario || {};
    return base.map((task) => {
      const outcome = per[task.scenarioId] || per[task.id];
      /* Only what the trial changed moves; unchanged scenarios keep their
         own outcome (passing, flaky or failing). */
      if (!outcome || !TRIAL_CHANGES[outcome]) return task;
      const passed = TRIAL_CHANGES[outcome] === "passed";
      return {
        ...task,
        status: passed ? "passed" : "failed",
        verdict: passed ? "passed" : "failed",
        passShare: passed ? 1 : 0,
      };
    });
  }, [readOnly, env, envState, stored, trialMatch]);

  const storedStats = useMemo(() => {
    /* Pulled (test invalid twice) and Outdated / Re-check scenarios are left
       out of every count. Runs from before them have neither. */
    const stale = stored?.staleScenarios || {};
    const counted = storedTasks.filter((t) => !t.pulled && !stale[t.id]);
    const passed = counted.filter((t) => t.status === "passed").length;
    const failed = counted.filter((t) => t.status === "failed").length;
    const flaky = counted.filter((t) => t.status === "flaky").length;
    const unmeasured = counted.filter((t) => t.status === "unmeasured").length;
    const measured = counted.filter((t) => t.status !== "unmeasured");
    return {
      total: storedTasks.length,
      passed,
      failed,
      flaky,
      unmeasured,
      measured: measured.length,
      repeats: stored?.repeats || 1,
      /* Same rule as the live run: the mean of the per-scenario proportions,
         over the scenarios that produced one. */
      passRate: measured.length
        ? measured.reduce(
            (a, t) => a + (t.passShare ?? (t.status === "passed" ? 1 : 0)),
            0,
          ) / measured.length
        : 0,
      tokens: storedTasks.reduce((a, t) => a + (t.tokens || 0), 0),
      cost: storedTasks
        .filter((t) => !t.costUnverified)
        .reduce((a, t) => a + (t.cost || 0), 0),
    };
  }, [storedTasks, stored]);

  /*
    Finishing hands the run to the execution-detail screen and goes there.

    That screen is real product code and fetches everything it renders, so the
    run is published to the mock adapter first — the adapter answers those
    fetches for this run id and passes every other request through untouched.
    Publishing before navigating matters: the screen's first query fires on
    mount, and an empty answer would be cached.
  */
  useEffect(() => {
    if (phase !== "done" || watchTrial) return;
    installMockExecutionAdapter();
    publishRun(runId, {
      tasks,
      startedAt: new Date(Date.now() - elapsed).toISOString(),
    });
  }, [phase, runId, tasks, elapsed, watchTrial]);

  /*
    Record the run as soon as the simulation actually starts (phase
    transitions from "booting" to "running") so it appears in the env's
    Run history and the Simulated Runs list immediately — even if the
    user navigates away or closes the tab before it completes. The
    reducer is upsert-by-id, so the finaliser effect below merges the
    completion stats into this same row.
  */
  const startedRef = useRef(false);
  useEffect(() => {
    if (phase !== "running" || startedRef.current || watchTrial) return;
    /* A pending group member is already recorded, but only as a placeholder —
       it gets the full start record like any new run. */
    if (envState.runs.some((r) => r.id === runId && !r.pending)) {
      startedRef.current = true;
      return;
    }
    startedRef.current = true;
    const totalTasks = tasks.length || scenarios.length * repeats;
    recordRun({
      id: runId,
      ...(runBudget && {
        budget: {
          /* A group member keeps what the group recorded (its share of the
             shared limit, groupCap). */
          ...(pendingMember?.budget || {}),
          cap: runBudget.cap,
          alertAt: runBudget.alertAt,
          /* The run plan's estimate, kept for the Cost tab's comparison. */
          estimate: (() => {
            const e = estimateRunPlan({
              env,
              envState,
              scenarios: scenarios.length,
              attempts: repeats,
              versions: [runAgent].filter(Boolean),
              evals: runEvals,
            });
            return {
              low: e.platform.low,
              high: e.platform.high,
              trials: e.trials,
            };
          })(),
          /* The rate plan it was priced at — a later rate change doesn't
             reprice this run. */
          rates: `${RATE_PLAN.name} · ${RATE_PLAN.version}`,
          /* Where the agent ran — hosted sandbox is measured, not billed. */
          runLocation: envState?.agent?.values?.runLocation || null,
        },
      }),
      /* Finishing what an earlier run's limit left unstarted — those
         scenarios only; the ones that finished there aren't run again. */
      ...(params.get("continues") && { continues: params.get("continues") }),
      label: only
        ? `${totalTasks} scenario${totalTasks === 1 ? "" : "s"} re-run`
        : `${env?.name} · ${totalTasks} scenario${totalTasks === 1 ? "" : "s"}`,
      status: "running",
      startedAt: new Date().toISOString(),
      /* Who started it — the runs table's By column. */
      startedBy: currentUser().name,
      via: "console",
      total: totalTasks,
      passed: 0,
      failed: 0,
      flaky: 0,
      unmeasured: 0,
      agentVersion: runAgent,
      envVersion: runEnv,
      ...(candidate && {
        candidateSource: {
          kind: candidate.kind,
          sha: candidate.sha,
          param: candidate.param,
          label: candidate.label,
          detail: candidate.detail,
          ...(candidate.name && { name: candidate.name }),
        },
      }),
      /* Everything else it held fixed — suite, sub-goal catalogue, eval
         version, simulated caller, scenario versions, surface, fidelity,
         trials, seed — so Compare can say what matched. An exact snapshot
         re-run holds the source run's pins, not today's. */
      ...(snapshotSrc
        ? {
            ...pinsForNewRun(env, envState, {
              trials: repeats,
              seed: replaySeed,
            }),
            ...Object.fromEntries(
              [
                "admissionGates",
                "actorEdits",
                "suiteVersion",
                "catalogVersion",
                "evalVersion",
                "simulator",
                "toolEnv",
                "toolEnvSummary",
                "toolFaults",
                "scenarioVersions",
                "surface",
                "runFidelity",
                "outcomes",
                "requiredEvals",
                "proofKind",
                "staleScenarios",
                "gradingRule",
                "turnLimits",
                "turnLimit",
                "runSettings",
                "secrets",
              ]
                .filter((k) => snapshotSrc[k] !== undefined)
                .map((k) => [k, snapshotSrc[k]]),
            ),
            snapshotOf: snapshotSrc.id,
            replaySeed,
          }
        : {
            /* A run from the Run button takes the updated RSI PRD's run rules,
             and pins the scenarios that were stale when it started. */
            ...pinsForNewRun(env, envState, {
              trials: repeats,
              seed: runId,
              rules: true,
            }),
            ...(liveStale && { staleScenarios: liveStale }),
          }),
      /* Run anyway on a world that can't answer some of the agent's tools —
         recorded; calls to them come back as not measured. */
      toolGap: toolFit(env, envState, {
        agent: runAgent,
        envVersion: runEnv,
      }).missing.map((t) => t.name),
      /* The graders applied when it ran — a later eval change must not
         regrade history. */
      evals: snapshotSrc?.evals || envState.evals,
      /* And the rules that graded it — confirming a held rule later changes
         the test, and Compare needs to be able to say so. */
      rules: gradedRuleIds(env, envState),
      /* And the cast of actors it ran with. */
      actors: inputs.actors.map((a) => a.id),
      scenarioIds: scenarios.map((sc) => sc.id),
      /* Left out because they aren't proved on this world. */
      skippedIds: skipped.map((sc) => sc.id),
      repeats,
      /* Over 200 conversations: how many it ran at once. */
      ...(overCap && { atOnce: parallel }),
      partial: !!only,
      ordinal:
        pendingMember?.ordinal ??
        Math.max(0, ...(envState.runs || []).map((r) => r.ordinal || 0)) + 1,
      seed: replaySeed || runId,
      /* The run plan's limits, as it started. */
      limits: {
        trialTimeout: Number(params.get("timeout")) || 10,
        runDeadline: Number(params.get("deadline")) || 0,
        turnLimit: envTurnLimitOf(envState),
        stepLimit: Number(params.get("steps")) || null,
      },
      twinWrites: null,
      ...(pendingMember && { pending: false }),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, runId]);

  /* Merge the completion stats into the run row when it finishes.
     Ordinal, agentVersion, envVersion, scenarioIds, repeats and the
     seed were already stamped by the start effect above — the upsert
     reducer preserves them. This write only overwrites the fields that
     genuinely change at completion (status, finishedAt, counts, twin
     writes, and the label to match the final task count). */
  /*
    A fix's first step, watched to its end: the fix carries on, so its page is
    where to be — this run's results stay a link away (its Reproduce step,
    the environment's Runs). A moment's pause so the run and the fix's start
    are saved before the page changes. Read back later, it never moves.
  */
  useEffect(() => {
    if (phase !== "done" || watchTrial || !fixFirstRun || !wasLive.current)
      return undefined;
    const fix = (envState.optimizations || []).find(
      (o) => o.fromRunId === runId,
    );
    if (!fix) return undefined;
    const t = setTimeout(
      () =>
        navigate(improvementPath(envId, fix.id), {
          replace: true,
          state: { fromIssue: fix.trigger?.issueId || null },
        }),
      900,
    );
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, runId]);

  /* A fix waiting on this run starts its search now (it never runs ahead). */
  useEffect(() => {
    if (phase !== "done") return;
    const list = envState.optimizations || [];
    if (!list.some((o) => o.waitingForRun === runId)) return;
    const at = new Date().toISOString();
    patch({
      optimizations: list.map((o) =>
        o.waitingForRun === runId
          ? { ...o, waitingForRun: undefined, startedAt: at }
          : o,
      ),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, runId]);
  useEffect(() => {
    if (phase !== "done" || watchTrial) return;
    recordRun({
      id: runId,
      label: only
        ? `${stats.total} scenario${stats.total === 1 ? "" : "s"} re-run`
        : `${env?.name} · ${stats.total} scenario${stats.total === 1 ? "" : "s"}`,
      /* A run where nothing could be measured passed nothing. */
      status:
        stats.total - stats.unmeasured > 0 && stats.failed === 0 && !stats.flaky
          ? "passed"
          : "failed",
      finishedAt: new Date().toISOString(),
      /* What the limit did: used, why it stopped, what never started. */
      ...(spend?.cap && {
        budget: {
          ...(recorded?.budget || {}),
          cap: spend.cap,
          alertAt: runBudget?.alertAt || 0.8,
          used: spend.used,
          stoppedBy: spend.reached || null,
          notStarted: notStartedIds,
        },
      }),
      /* The environment's own scenarios; a fix's issue scenarios aren't counted. */
      total: (stats.envTotal ?? stats.total) - (stats.notStarted || 0),
      passed: stats.passed,
      failed: stats.failed,
      flaky: stats.flaky,
      unmeasured: stats.unmeasured,
      twinWrites: envState.twinBacking
        ? tasks.reduce((sum, task) => {
            const t = twinTimelineFor(envState, task);
            return (
              sum + Object.values(t.writesByService).reduce((a, b) => a + b, 0)
            );
          }, 0)
        : null,
    });
    // The spend is final once the phase is done; it isn't a reason to re-record.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, runId, env, envState, stats, scenarios, only, recordRun]);

  /*
    The rest of its group. Members ran alongside this one; any that nobody
    watched stream are settled now — rebuilt from their own ids, the way a
    finished run is always read back — so every run in the group is finished
    and in its environment's history, and switching to one reads it.
  */
  const settledRef = useRef(false);
  useEffect(() => {
    if (phase !== "done" || settledRef.current || watchTrial) return;
    settledRef.current = true;
    const members = groupMembersOf(state, recorded || pendingMember);
    (members || []).forEach((m) => {
      if (
        m.runId === runId ||
        !m.run ||
        m.run.finishedAt ||
        !m.env ||
        !m.envState
      )
        return;
      commit({
        type: "recordRun",
        envId: m.envId,
        run: settledRun(m.env, m.envState, m.run),
      });
    });
  }, [phase]); // eslint-disable-line react-hooks/exhaustive-deps

  /*
    Tell the team. Posted once per run, to whichever Slack channel the
    workspace routes each kind of message to (no-op without Slack): the run
    itself, a regression against the previous agent version on the same
    scenarios, and any critical scenario that failed. Information only —
    nothing here blocks or ships anything.
  */
  const notifiedRef = useRef(false);
  useEffect(() => {
    if (phase !== "done" || notifiedRef.current || watchTrial) return;
    notifiedRef.current = true;
    /*
      The run digest (AC-20.8): one compact message — score, the run summary
      (pass^1 · pass^k, counts by cause), the top three issues, deep links — to wherever the workspace
      already routes "Simulation finished". Never one per scenario, and not
      for an improvement's candidate runs, which run by the dozen.
    */
    /* An improvement's candidate runs post nothing — not the digest, not a
       regression, not a release blocker. They run by the dozen, and the
       improvement says what they found. */
    const candidateRun = isCandidateRun(
      { ...(recorded || {}), id: runId },
      envState,
    );
    if (candidateRun) return;
    const integrations = getIntegrations();
    const rep = runReportOf({
      env,
      envState,
      runId,
      tasks,
      summaries: runSummaries(env, envState),
      goalOf: deriveUseCaseLabel,
      integrations,
    });
    postSlack({
      event: "run_finished",
      title: rep.digest.title,
      lines: rep.digest.lines,
      actions: rep.digest.links.map((l) => l.label),
      links: Object.fromEntries(rep.digest.links.map((l) => [l.label, l.href])),
    });
    /* Where it went, and any question the run asked — kept on the record. */
    recordRun({
      id: runId,
      digest: digestDestinationsOf(integrations),
      ...(clarifications.length && { clarifications }),
    });
    /* Against the latest run of another agent version on the same
       environment version, read the way Compare reads it — the alert fires
       only on Compare's "Regressed", so it never calls noise (or a different
       environment version) a regression. */
    const summaries = runSummaries(env, envState);
    const current = {
      ...(summaries.find((r) => r.id === runId) || recorded || {}),
      id: runId,
      agentVersion: runAgent,
      tasks,
      repeats:
        recorded?.repeats ||
        summaries.find((r) => r.id === runId)?.repeats ||
        1,
    };
    const version = snapshotLabelOf(current, env, envState);
    const prev = summaries
      .filter(
        (r) =>
          r.id !== runId &&
          r.finishedAt &&
          !r.pending &&
          r.agentVersion !== runAgent &&
          !isCandidateRun(r, envState),
      )
      .sort((x, y) => Date.parse(y.finishedAt) - Date.parse(x.finishedAt))
      .find((r) => snapshotLabelOf(r, env, envState) === version);
    const read = prev ? comparisonOf([prev, current], env, envState) : null;
    if (read?.label.id === "regressed") {
      const down = Math.abs(Math.round(read.interval.delta));
      postSlack({
        event: "regression",
        title: `Regressed · agent ${runAgent} is down ${down} pts`,
        lines: [
          `${env?.name}: ${runIdentityOf(prev, envState, env).label} → agent ${runAgent}, on ${read.interval.n} comparable scenarios`,
        ],
        actions: ["Compare runs", "View failures"],
        links: {
          "Compare runs": `${paths.dashboard.simulate.simulationCompare(envId)}?runs=${prev.id},${runId}`,
          "View failures": `${paths.dashboard.simulate.simulationRun(envId, runId)}?tab=tasks`,
        },
      });
    }
    /* Must-pass — critical, or kept by regression memory — not "critical"
       alone; Settings calls this event "Must-pass scenario failing". */
    const scenarioById = new Map(
      (envState.scenarios || []).map((sc) => [sc.id, sc]),
    );
    const mustPass = (t) => {
      const sc = scenarioById.get(t.scenarioId || t.id);
      return !!(
        t.critical ||
        t.regressionMemory ||
        sc?.critical ||
        sc?.regressionMemory
      );
    };
    const failing = tasks.filter(
      (t) => mustPass(t) && t.status !== "passed" && t.status !== "unmeasured",
    );
    if (failing.length) {
      postSlack({
        event: "release_blocker",
        title: `${failing.length} must-pass scenario${failing.length === 1 ? "" : "s"} failing · ${env?.name}`,
        lines: failing
          .slice(0, 3)
          .map((t) => scenarioTitle(t.title) || t.name || t.id),
        actions: ["View failures"],
        links: {
          "View failures": `${paths.dashboard.simulate.simulationRun(envId, runId)}?tab=tasks`,
        },
      });
    }
  }, [phase]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Start straight away — no boot screen. A failing credential asks first.
     Only a new run, once the store has loaded: before that every run looks
     new and empty, and one read back from history is never played again. */
  useEffect(() => {
    if (phase !== "booting" || !state.hydrated || readOnly || !scenarios.length)
      return;
    if (failedSecrets.length && !secretsOk) setSecretAsk(true);
    else start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, state.hydrated, readOnly, scenarios.length]);

  /* A finished run stays on its own results (below) — the run someone just
     watched is the one they want to read; the list is one click back. */

  if (!env) {
    /* Wait for hydration before declaring the env missing — a custom env
       exists only in the cached myEnvironments, which is empty until the
       store loads (and reloads on every HMR patch while editing). */
    if (!state.hydrated) {
      return (
        <Box
          sx={{
            p: 2,
            height: "100%",
            minHeight: 420,
            display: "grid",
            placeItems: "center",
          }}
        />
      );
    }
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Environment not found"
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => navigate(paths.dashboard.simulate.environments)}
            >
              Back to environments
            </Button>
          }
        />
      </Box>
    );
  }

  if (envState.scenarios.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:layers-minimalistic-linear"
          title="Nothing to run"
          body="This environment has no scenarios selected yet."
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() =>
                navigate(
                  paths.dashboard.simulate.environmentStep(envId, "scenarios"),
                )
              }
            >
              Add scenarios
            </Button>
          }
        />
      </Box>
    );
  }

  if (!readOnly && !readiness.ready) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Not ready to run"
          body={readiness.reason}
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() =>
                navigate(
                  readiness.href ||
                    paths.dashboard.simulate.environmentStep(
                      envId,
                      readiness.step,
                    ),
                  { replace: true },
                )
              }
            >
              {readiness.href ? "Open the agent" : "Open the environment"}
            </Button>
          }
        />
      </Box>
    );
  }

  if (!readOnly && plan.selectionMissing) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:layers-minimalistic-linear"
          title="Nothing selected is in this version"
          body={`None of the selected scenarios are part of environment ${runEnvShown} — they were added on a later version. Pick others, or switch environment version.`}
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() =>
                navigate(
                  paths.dashboard.simulate.environmentStep(envId, "scenarios"),
                )
              }
            >
              Go to scenarios
            </Button>
          }
        />
      </Box>
    );
  }

  if (!readOnly && scenarios.length === 0 && skipped.length) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:shield-cross-linear"
          title="Nothing proved to run"
          body={`${skipped.length === 1 ? "The 1 scenario" : `All ${skipped.length} scenarios`} ${skipped.length === 1 ? "is" : "are"} stale, outdated or not yet proven on environment ${runEnvShown}, so a run couldn't give ${skipped.length === 1 ? "it" : "them"} a verdict.`}
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() =>
                navigate(
                  paths.dashboard.simulate.environmentStep(envId, "scenarios"),
                )
              }
            >
              Go to scenarios
            </Button>
          }
        />
      </Box>
    );
  }

  /* ── a finished run, read back ── */
  if (readOnly) {
    return (
      <RunResults
        env={env}
        runId={runId}
        tasks={storedTasks}
        stats={storedStats}
        evals={evals}
      />
    );
  }

  /* ── pre-run provisioning ── */
  /* No "Preparing the simulation" screen — a run starts as soon as it
     opens (the effect above). Only a failing credential asks first. */
  if (phase === "booting") {
    return (
      <Box sx={{ height: "100%" }}>
        <SecretCheckDialog
          open={secretAsk}
          failed={failedSecrets}
          onClose={() => {
            setSecretAsk(false);
            navigate(paths.dashboard.simulate.environmentDetail(envId));
          }}
          onRun={() => {
            setSecretAsk(false);
            setSecretsOk(true);
            start();
          }}
          onFix={() => {
            setSecretAsk(false);
            navigate(
              paths.dashboard.simulate.environmentStep(envId, "settings"),
            );
          }}
        />
      </Box>
    );
  }

  /* ── results ── */
  if (phase === "done") {
    /* Scenarios the spending limit kept from starting never ran — results
       read only what did, as the run reads back later. */
    const ran = stats.notStarted
      ? tasks.filter((t) => t.status !== "notStarted")
      : tasks;
    return (
      <RunResults
        env={env}
        runId={runId}
        tasks={ran}
        stats={
          stats.notStarted
            ? { ...stats, total: stats.total - stats.notStarted }
            : stats
        }
        evals={evals}
      />
    );
  }

  /* ── live ── */
  const live = focus && ["running", "grading"].includes(focus.status);

  /* The run's number — the same one the Runs tab lists it under — and its
     name, "agent v3 × env v7", the way that row will name it. */
  const ordinal =
    watchTrial?.trial?.n ??
    (stored || pendingMember)?.ordinal ??
    Math.max(0, ...(envState.runs || []).map((r) => r.ordinal || 0)) + 1;
  const named = runIdentityOf(
    {
      ...(stored || {}),
      id: runId,
      agentVersion: runAgent,
      envVersion: runEnv,
      agentName: stored?.agentName,
    },
    envState,
    env,
  );

  /* Critical scenarios, counted as they settle. One failing is worth knowing
     before the run ends — it informs; it blocks nothing. */
  const blockers = tasks.filter((t) => t.critical);
  const blockersPassed = blockers.filter((t) => t.status === "passed").length;
  const blockersFailed = blockers.filter((t) => t.status === "failed");

  /* Sealed · Live · Prompt only: a new run records it as it starts; a stored
     one shows what it pinned. */
  const liveProof = (() => {
    const p = runProofKindOf(pinSource, envState);
    return p && !pinSource ? { ...p, pinned: true } : p;
  })();

  /* Outdated / Re-check scenarios in this run — "3 outdated scenarios". */
  const staleCount = Object.keys(staleIds || {}).filter((id) =>
    tasks.some((t) => t.id === id),
  ).length;
  const staleKinds = ["outdated", "recheck"]
    .map((k) => [
      k,
      Object.entries(staleIds || {}).filter(
        ([id, v]) => v === k && tasks.some((t) => t.id === id),
      ).length,
    ])
    .filter(([, n]) => n);
  const staleLine = `${staleKinds.map(([k, n]) => `${n} ${k === "outdated" ? "outdated" : "re-check"}`).join(" and ")} ${staleCount === 1 ? "scenario" : "scenarios"}`;
  /* Pulled scenarios settle into their own group in the left column. */
  const pulledDone = tasks.filter((t) => t.pulled && t.status === "unmeasured");
  const listed = tasks.filter((t) => !pulledDone.includes(t));
  const shownTasks = listed.filter((t) =>
    matchesTrialFilters(
      t,
      trialFilter,
      t.status !== "notStarted" &&
        !!approvalOf({ env, envState, task: t })?.pending,
    ),
  );

  /* Tools the agent calls that this world can't answer — the run was started
     anyway, and scenarios that reach for them come back not measured. */
  const toolGap = toolFit(env, envState, {
    agent: runAgent,
    envVersion: runEnv,
  }).missing.map((t) => t.name);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      {/* Its environment was built for an Error Feed issue's Fix. */}
      <IssueReturnBanner envId={envId} />
      {/* ── run header ── */}
      <Box
        sx={{
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        {/* Two short lines at any width: what's running and how it's going.
            Repeats, the expected length and the over-200 note sit in the
            subtitle's tooltip; outcome counts show only once they're above 0
            (Running, Passed and Failed always). */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 3, py: 1.25 }}
        >
          <IconButton
            size="small"
            aria-label="Back"
            onClick={() => {
              /* Go back to the surface that opened this run — Improvements L2,
                 the env workspace's Runs tab, or wherever else. Falls back to
                 the env's Runs tab on cold-load (no history). */
              if (window.history.length > 1) navigate(-1);
              else
                navigate(
                  paths.dashboard.simulate.environmentStep(envId, "runs"),
                );
            }}
          >
            <Iconify
              icon="solar:alt-arrow-left-linear"
              width={18}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>

          <Box minWidth={0} flex={1}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ minWidth: 0, flexWrap: "nowrap" }}
            >
              <Typography
                noWrap
                sx={{
                  fontSize: 16,
                  lineHeight: "24px",
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {watchTrial
                  ? `${watchTrial.opt.name || "Improvement"} · Trial ${ordinal}`
                  : `Run ${ordinal}`}
              </Typography>
              {/* Waiting on an answer is the run's state, not another
                  line — the question itself is the card below. */}
              <StatusChip status={blocked ? "needs_input" : "running"} />
              {/* What the run proves, as a mark — the words on hover. */}
              <ProofKindChip proof={liveProof} compact />
              {/* The candidate came from a code snapshot, not a version. */}
              {candidate && (
                <Tooltip
                  arrow
                  title={`${candidate.label} — ${candidate.detail}`}
                >
                  <Box
                    component="span"
                    aria-label={candidate.label}
                    sx={{
                      display: "inline-flex",
                      color: "text.tertiary",
                      flexShrink: 0,
                    }}
                  >
                    <Iconify icon="solar:code-square-linear" width={16} />
                  </Box>
                </Tooltip>
              )}
            </Stack>
            <Tooltip
              arrow
              placement="bottom-start"
              title={[
                `${named.label}`,
                partial
                  ? `Re-run · ${scenarios.length} of ${poolSize} scenarios`
                  : null,
                liveProof?.kind !== "live" ? SHADOW_SUMMARY : null,
                `${stats.done} of ${stats.total} scenarios done${repeats > 1 ? ` · ${repeats} repeats each` : ""}`,
                outcomeLines(stats).join(" · "),
                `${formatMs(elapsed)} elapsed${size.voice ? ` · expected ${voiceLine}` : ""}`,
                spend?.cap
                  ? `Credits · ${spend.used.toLocaleString()} used · ~${spend.running.toLocaleString()} held by running scenarios · limit ${spend.cap.toLocaleString()}${groupCap ? ` (share of the group's ${groupCap.toLocaleString()})` : ""} · ${Math.max(0, spend.cap - spend.used - spend.running).toLocaleString()} left`
                  : null,
                overCap
                  ? `${parallel} at once · you can leave this page; the run is saved in Runs`
                  : null,
                phase === "running" && !blocked
                  ? stalled
                    ? `No heartbeat for ${sinceLastEvent}s — the run may be stalled`
                    : `Healthy · last event ${sinceLastEvent}s ago`
                  : null,
              ]
                .filter(Boolean)
                .join("\n")}
              slotProps={{ tooltip: { sx: { whiteSpace: "pre-line" } } }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.75}
                sx={{ minWidth: 0, cursor: "default" }}
              >
                {phase === "running" && !blocked && (
                  <Box
                    aria-hidden
                    sx={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      flexShrink: 0,
                      bgcolor: stalled
                        ? "status.danger.solid"
                        : "status.success.solid",
                      animation: stalled
                        ? "none"
                        : `${pulse} 1.6s ease-in-out infinite`,
                    }}
                  />
                )}
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
                >
                  {/* The numbers lead, so a narrow screen clips the where,
                      not the outcome. */}
                  {`${stats.done}/${stats.total} done · `}
                  <Box
                    component="span"
                    sx={{
                      fontWeight: 600,
                      color: stats.passed ? "status.success.fg" : "inherit",
                    }}
                  >
                    {stats.passed}
                  </Box>
                  {" passed · "}
                  <Box
                    component="span"
                    sx={{
                      fontWeight: 600,
                      color: stats.failed ? RED : "inherit",
                    }}
                  >
                    {stats.failed}
                  </Box>
                  {` failed · ${formatMs(elapsed)}`}
                  {spend?.cap ? (
                    <Box
                      component="span"
                      sx={{
                        color: spend.reached ? "status.warning.fg" : "inherit",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {` · ${spend.used.toLocaleString()} / ${spend.cap.toLocaleString()} credits`}
                    </Box>
                  ) : null}
                  {` · ${env.name}`}
                  {named.improvement && (
                    <>
                      {" · "}
                      <FromImprovement improvement={named.improvement} />
                    </>
                  )}
                  {` · agent ${runAgent}`}
                  {phase === "running" && !blocked && stalled && (
                    <Box component="span" sx={{ color: "status.danger.fg" }}>
                      {` · No heartbeat for ${sinceLastEvent}s`}
                    </Box>
                  )}
                </Typography>
              </Stack>
            </Tooltip>
          </Box>

          <Tooltip
            title={
              spend?.cap
                ? `Stop — no new ${size.voice ? "calls" : "scenarios"} start; running ones finish and are charged, the rest aren't`
                : "Stop run"
            }
            arrow
          >
            <IconButton
              size="small"
              aria-label="Stop run"
              onClick={
                spend?.cap && spend.reached !== "stopped"
                  ? stopAtLimit
                  : finishNow
              }
            >
              <Iconify
                icon="solar:stop-circle-linear"
                width={20}
                sx={{ color: "status.danger.fg" }}
              />
            </IconButton>
          </Tooltip>
        </Stack>

        {/* Progress is the header's bottom edge, coloured by outcome. */}
        <OutcomeBar stats={stats} />
      </Box>

      {/* ── the run group it was started with, if any ── */}
      <RunGroupBar envId={envId} runId={runId} live />

      {/* ── what to know about this run ── */}
      {(fixRun ||
        watchTrial ||
        blockers.length > 0 ||
        toolGap.length > 0 ||
        skipped.length > 0 ||
        stats.retried > 0 ||
        stats.pulled > 0 ||
        staleCount > 0) && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={2.5}
          flexWrap="wrap"
          rowGap={0.75}
          sx={{
            px: 3,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          {/* A candidate being watched: what it is, and the way back. */}
          {watchTrial && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`Watching Trial ${watchTrial.trial.n} of ${watchTrial.opt.name || "this improvement"} — the change it tried, replayed on the scenarios it was tested on. Nothing here is saved. · `}
              <Link
                component="button"
                type="button"
                underline="hover"
                onClick={() =>
                  navigate(improvementPath(envId, watchTrial.opt.id), {
                    state: {
                      back: {
                        to: `${paths.dashboard.simulate.simulationRun(envId, runId)}?tab=improvements`,
                        label: recorded?.ordinal
                          ? `Run ${recorded.ordinal}`
                          : "the run",
                      },
                    },
                  })
                }
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.primary",
                  verticalAlign: "baseline",
                }}
              >
                Back to the improvement
              </Link>
            </Typography>
          )}
          {/* A fix's first run: what it's for, first. */}
          {fixRun && (
            <FixRunNote
              envId={envId}
              run={recorded || pendingMember}
              issue={stats.issue}
            />
          )}
          {blockers.length > 0 && (
            <Tooltip
              arrow
              title={
                blockersFailed.length
                  ? `Failed: ${blockersFailed.map((t) => scenarioTitle(t.title) || t.name).join(", ")}. Counted in the run summary — what to do about it is your call.`
                  : "Scenarios marked critical, counted as they settle. They inform the report; nothing is blocked."
              }
            >
              <Stack direction="row" alignItems="center" spacing={0.625}>
                <Iconify
                  icon="solar:danger-triangle-bold"
                  width={14}
                  sx={{ color: "status.danger.fg" }}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  Critical scenarios ·{" "}
                  <b>
                    {blockersPassed} of {blockers.length}
                  </b>{" "}
                  passed
                </Typography>
                {blockersFailed.length > 0 && (
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "status.danger.fg",
                    }}
                  >
                    · {blockersFailed.length} failed
                  </Typography>
                )}
              </Stack>
            </Tooltip>
          )}
          {toolGap.length > 0 && (
            <Stack direction="row" alignItems="center" spacing={0.625}>
              <Iconify
                icon="solar:plug-circle-linear"
                width={14}
                sx={{ color: "status.warning.fg" }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                Environment {runEnvShown} can&apos;t answer {toolGap.join(", ")}{" "}
                — scenarios that call {toolGap.length === 1 ? "it" : "them"}{" "}
                come back not measured.
              </Typography>
            </Stack>
          )}
          {stats.retried > 0 && (
            <Tooltip
              arrow
              title="A conversation that ends test invalid or error runs once more before it's read. Neither counts for or against the agent."
            >
              <Stack direction="row" alignItems="center" spacing={0.625}>
                <Iconify
                  icon="solar:restart-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {`${stats.retried} retried once`}
                </Typography>
              </Stack>
            </Tooltip>
          )}
          {stats.pulled > 0 && (
            <Tooltip
              arrow
              title="Test invalid twice — taken out of this run and flagged for review. Left out of every count."
            >
              <Stack direction="row" alignItems="center" spacing={0.625}>
                <Iconify
                  icon="solar:flag-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {`${stats.pulled} pulled for review`}
                </Typography>
              </Stack>
            </Tooltip>
          )}
          {staleCount > 0 && (
            <Stack direction="row" alignItems="center" spacing={0.625}>
              <Iconify
                icon="solar:history-linear"
                width={14}
                sx={{ color: "text.tertiary" }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {`${staleLine} ran but count toward no decision until updated or archived`}
              </Typography>
            </Stack>
          )}
          {skipped.length > 0 && (
            <Tooltip
              arrow
              title="Scenarios are re-proved when a run starts, and only proved ones run. Fix these on the Scenarios tab to include them."
            >
              <Stack direction="row" alignItems="center" spacing={0.625}>
                <Iconify
                  icon="solar:shield-cross-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {skipped.length} skipped — can&apos;t stage on environment{" "}
                  {runEnvShown}
                </Typography>
              </Stack>
            </Tooltip>
          )}
        </Stack>
      )}

      {/* ── the spending limit: its warning, or the limit reached ── */}
      <SpendCard
        spend={spend}
        stats={stats}
        voice={["voice", "multi"].includes(env?.surface)}
        onContinue={dismissAlert}
        onLowerLimit={setCap}
        onStop={stopAtLimit}
        providerName={runBudget?.providerName}
      />

      {/* ── a scenario waiting on a clarifying question ── */}
      <ClarifyCard
        blocked={blocked}
        scenario={blocked ? tasks.find((t) => t.id === blocked.taskId) : null}
        onAnswer={answer}
        onOpen={() => blocked && setFocusId(blocked.taskId)}
      />

      {/* ── conversations waiting for an approver ── */}
      {(() => {
        const waiting = tasks.filter(
          (t) =>
            t.status !== "notStarted" &&
            approvalOf({ env, envState, task: t })?.pending,
        );
        if (!waiting.length) return null;
        const first = approvalOf({ env, envState, task: waiting[0] });
        return (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              mx: 2,
              mb: 1,
              px: 1.5,
              py: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            <Iconify
              icon="solar:clock-circle-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", flex: 1 }}>
              {`Waiting for approval · ${waiting.length} conversation${waiting.length === 1 ? "" : "s"} — ${first.approver.name} answers within ${first.approver.timeoutMin || 3} min, or decide as them from the conversation.`}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={() => setFocusId(waiting[0].id)}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Open
            </Button>
          </Stack>
        );
      })()}

      {/* ── three columns ── */}
      <Box sx={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* scenarios */}
        <Box
          sx={{
            width: 300,
            flexShrink: 0,
            borderRight: "1px solid",
            borderColor: "divider",
            overflow: "auto",
            display: { xs: "none", md: "block" },
          }}
        >
          <Box
            sx={{
              position: "sticky",
              top: 0,
              zIndex: 1,
              bgcolor: "background.default",
            }}
          >
            <ColumnLabel>Scenarios ({stats.total})</ColumnLabel>
            <TrialFilters
              value={trialFilter}
              onChange={setTrialFilter}
              actors={actorsInRun(tasks)}
              shown={shownTasks.length}
              total={listed.length}
            />
          </Box>
          {shownTasks.length === 0 && listed.length > 0 && (
            <Typography
              sx={{ px: 2, py: 1.5, typography: "s3", color: "text.tertiary" }}
            >
              No scenario matches these filters.
            </Typography>
          )}
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {shownTasks.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                active={t.id === focusId}
                stale={staleIds?.[t.id] || null}
                onClick={() => setFocusId(t.id)}
              />
            ))}
          </Stack>
          {pulledDone.length > 0 && (
            <>
              <ColumnLabel>Pulled for review ({pulledDone.length})</ColumnLabel>
              <Stack
                divider={
                  <Box
                    sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                  />
                }
              >
                {pulledDone.map((t) => (
                  <TaskRow
                    key={t.id}
                    task={t}
                    active={t.id === focusId}
                    onClick={() => setFocusId(t.id)}
                  />
                ))}
              </Stack>
            </>
          )}
          {skipped.length > 0 && (
            <>
              <ColumnLabel>Skipped ({skipped.length})</ColumnLabel>
              <Stack
                divider={
                  <Box
                    sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                  />
                }
              >
                {skipped.map((sc) => (
                  <SkippedRow key={sc.id} scenario={sc} />
                ))}
              </Stack>
            </>
          )}
        </Box>

        {/* stage */}
        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {focus && (
            <>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{
                  px: 2.5,
                  py: 1.5,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                <StatusDot status={focus.status} />
                <Box minWidth={0} flex={1}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                    {scenarioTitle(focus.title) || focus.name}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {focus.task}
                  </Typography>
                </Box>
                <Tooltip
                  arrow
                  title={
                    repeats > 1
                      ? `The first repeat plays here turn by turn. The other ${repeats - 1} run alongside it and count toward the verdict.`
                      : ""
                  }
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      flexShrink: 0,
                    }}
                  >
                    {focus.stepIndex < 0
                      ? "Queued"
                      : `Turn ${focus.stepIndex + 1} of ${focus.steps.length}`}
                    {repeats > 1 ? ` · repeat 1 of ${repeats}` : ""}
                  </Typography>
                </Tooltip>
              </Stack>
              <Stage
                stage={surface.stage}
                task={focus}
                stepIndex={focus.stepIndex}
                live={live}
                twinBacking={envState?.twinBacking}
                agentVersion={`Agent ${runAgent}`}
              />
            </>
          )}
        </Box>

        {/* live evals */}
        <Box
          sx={{
            width: 288,
            flexShrink: 0,
            borderLeft: "1px solid",
            borderColor: "divider",
            overflow: "auto",
            display: { xs: "none", lg: "block" },
          }}
        >
          <LiveEvalPanel
            task={focus}
            evals={evals}
            env={env}
            grading={liveGrading}
          />
        </Box>
      </Box>
    </Box>
  );
}

/* ── pieces ──────────────────────────────────────────────────────────────── */

function ColumnLabel({ children, sticky }) {
  return (
    <Typography
      sx={{
        px: 2,
        py: 1.25,
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        textTransform: "none",
        letterSpacing: 0.4,
        borderBottom: "1px solid",
        borderColor: "divider",
        ...(sticky
          ? {
              position: "sticky",
              top: 0,
              bgcolor: "background.default",
              zIndex: 1,
            }
          : { borderTop: "1px solid", borderTopColor: "divider" }),
      }}
    >
      {children}
    </Typography>
  );
}

function Chip({ icon, label }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.75,
        height: 22,
        borderRadius: "4px",
        color: "text.tertiary",
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      {icon && <Iconify icon={icon} width={12} />}
      <Typography noWrap sx={{ typography: "s3", fontWeight: 600 }}>
        {label}
      </Typography>
    </Stack>
  );
}

const outcomeLines = (stats) => [
  `${stats.active || 0} running`,
  `${stats.passed} passed`,
  `${stats.failed} failed`,
  `${stats.flaky} flaky`,
  ...Object.entries(stats.notCounted || {})
    .filter(([, v]) => v > 0)
    .map(([k, v]) => `${v} ${(OUTCOMES[k]?.label || k).toLowerCase()}`),
  `${Math.max(0, (stats.total || 0) - (stats.done || 0))} left`,
];

/* The run's progress as a 3px edge under the header, filled by outcome —
   passed, flaky, failed, not counted — over the whole run. */
function OutcomeBar({ stats }) {
  const total = Math.max(1, stats.total || 0);
  const other = Object.values(stats.notCounted || {}).reduce(
    (n, v) => n + (v || 0),
    0,
  );
  const parts = [
    { key: "passed", n: stats.passed, color: "status.success.solid" },
    { key: "flaky", n: stats.flaky, color: "status.warning.solid" },
    { key: "failed", n: stats.failed, color: "status.danger.solid" },
    { key: "other", n: other, color: "text.disabled" },
  ];
  return (
    <Box
      role="img"
      aria-label={outcomeLines(stats).join(", ")}
      sx={{ display: "flex", height: 3 }}
    >
      {parts
        .filter((p) => p.n > 0)
        .map((p) => (
          <Box
            key={p.key}
            sx={{
              width: `${(p.n / total) * 100}%`,
              bgcolor: p.color,
              transition: "width .4s ease",
            }}
          />
        ))}
    </Box>
  );
}

/* How a finished scenario came out, across its repeats. */
const resultText = (task) => {
  if (task.pulled && task.status === "unmeasured")
    return { text: "Pulled", color: UNMEASURED };
  /* Inconclusive, test invalid or error — none counts for or against the agent. */
  if (task.status === "unmeasured")
    return {
      text: OUTCOMES[outcomeOfCause(causeOf(task))]?.label || "Inconclusive",
      color: UNMEASURED,
    };
  if (!["passed", "failed", "flaky"].includes(task.status)) return null;
  const color = { passed: "status.success.fg", failed: RED, flaky: FLAKY }[
    task.status
  ];
  return {
    text:
      task.repeats > 1
        ? `${task.passes}/${task.repeats}`
        : task.status === "passed"
          ? "Passed"
          : "Failed",
    color,
  };
};

function TaskRow({ task, active, stale = null, onClick }) {
  const progress = task.steps.length
    ? ((task.stepIndex + 1) / task.steps.length) * 100
    : 0;
  const result = resultText(task);
  return (
    <Box
      {...clickable(onClick)}
      sx={{
        px: 2,
        py: 1.375,
        cursor: "pointer",
        position: "relative",
        bgcolor: active ? "action.hover" : "transparent",
        "&:hover": { bgcolor: "action.hover" },
        ...focusRing,
      }}
    >
      {active && (
        <Box
          sx={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: 2,
            bgcolor: "primary.main",
          }}
        />
      )}
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <StatusDot status={task.status} size={7} />
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Typography
              noWrap
              sx={{ typography: "s2", fontWeight: active ? 600 : 500 }}
            >
              {scenarioTitle(task.title) || task.name}
            </Typography>
            {/* Marked critical — information for the reader, never a gate. */}
            {task.critical && (
              <Tooltip
                arrow
                title={`Critical scenario${task.blockerReason ? ` — ${task.blockerReason}` : ""}. Counted in the run summary; it blocks nothing.`}
              >
                <Box sx={{ display: "flex", flexShrink: 0 }}>
                  <Iconify
                    icon="solar:danger-triangle-linear"
                    width={12}
                    sx={{ color: "text.tertiary" }}
                  />
                </Box>
              </Tooltip>
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {[
              task.pulled && task.status === "unmeasured"
                ? "Test invalid twice"
                : null,
              task.retried &&
              ["passed", "failed", "flaky", "unmeasured"].includes(task.status)
                ? "retried once"
                : null,
              stale
                ? `${stale === "outdated" ? "Outdated" : "Re-check"} · not counted`
                : null,
              task.summary,
              task.persona?.name,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Box>
        {task.blocked && (
          <Tooltip
            arrow
            title="Paused on a clarifying question — answer it above to resume"
          >
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.secondary",
                flexShrink: 0,
              }}
            >
              Needs answer
            </Typography>
          </Tooltip>
        )}
        {result && (
          <Tooltip
            arrow
            title={
              task.repeats > 1
                ? `Passed ${task.passes} of ${task.repeats} repeats`
                : ""
            }
          >
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: result.color,
                flexShrink: 0,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {result.text}
            </Typography>
          </Tooltip>
        )}
      </Stack>

      {["running", "grading"].includes(task.status) && (
        <Box
          sx={{
            mt: 0.875,
            height: 2,
            borderRadius: 1,
            bgcolor: "background.neutral",
            overflow: "hidden",
          }}
        >
          <Box
            sx={{
              height: "100%",
              width: `${progress}%`,
              bgcolor: "primary.main",
              transition: "width .4s ease",
            }}
          />
        </Box>
      )}
    </Box>
  );
}

function SkippedRow({ scenario }) {
  return (
    <Tooltip arrow placement="right" title={scenario.skipReason}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ px: 2, py: 1.25, opacity: 0.7 }}
      >
        <Iconify
          icon="solar:shield-cross-linear"
          width={12}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            noWrap
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
          >
            {scenarioTitle(scenario.title) || scenario.name}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {scenario.skipReason}
          </Typography>
        </Box>
      </Stack>
    </Tooltip>
  );
}

/**
 * The right-hand column, in the order things happen.
 *
 * Sub-goals first: they move turn by turn, ticking on the line where the
 * agent actually did the thing. Evals next: they only score once the
 * conversation ends, one grader at a time. Then what a pass looks like, and
 * who is calling.
 */
function LiveEvalPanel({ task, evals, env, grading: rule = null }) {
  if (!task) return null;
  const grading = task.status === "grading";
  const settled = ["passed", "failed", "flaky", "unmeasured"].includes(
    task.status,
  );
  const basis = rule?.basis || null;
  const goals = subGoalStates(task, env).map((g) => ({
    ...g,
    basis: subGoalBasisOf(g.label, basis, env),
  }));

  return (
    <Box>
      {goals.length > 0 && (
        <>
          <ColumnLabel>
            Sub-goals · {goals.filter((g) => g.state === "met").length} of{" "}
            {goals.length}
          </ColumnLabel>
          {/* Which rule decides, and what the sub-goals are checked against. */}
          {rule && (
            <Tooltip
              arrow
              placement="left"
              title={[rule.note, basis?.note].filter(Boolean).join(" ")}
            >
              <Typography
                sx={{
                  px: 2,
                  pt: 1.25,
                  typography: "s3",
                  color: "text.tertiary",
                  cursor: "default",
                }}
              >
                {[
                  rule.rule >= 2
                    ? "Every critical sub-goal must hold"
                    : rule.label,
                  basis &&
                    (basis.judged
                      ? "judged from the conversation; only the starting state is checked"
                      : "checked against end state and tool calls"),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Tooltip>
          )}
          {task.turnLimit && (
            <Typography
              sx={{
                px: 2,
                pt: 0.5,
                typography: "s3",
                color: "status.danger.fg",
                fontWeight: 600,
              }}
            >
              {`Hit the turn limit (${task.turnLimit.limit}) — stopped here`}
            </Typography>
          )}
          <Stack spacing={1.125} sx={{ px: 2, py: 1.5 }}>
            {goals.map((g) => (
              <SubGoalRow key={g.id} goal={g} />
            ))}
          </Stack>
        </>
      )}

      <ColumnLabel>Evals</ColumnLabel>
      {evals.length === 0 ? (
        <EmptyState
          icon="solar:shield-cross-linear"
          title="No evals applied"
          body={
            rule?.rule >= 2
              ? "Sub-goals still decide pass or fail. Evaluations would score each conversation on top."
              : "You'll get transcripts, but nothing scoring whether the agent was right."
          }
        />
      ) : (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {task.evalResults.map((r, i) => {
            const resolved = (grading || settled) && i <= task.evalIndex;
            return (
              <Stack
                key={r.id}
                direction="row"
                alignItems="center"
                spacing={1.25}
                sx={{ px: 2, py: 1 }}
              >
                <Box
                  sx={{
                    width: 28,
                    height: 28,
                    borderRadius: "6px",
                    display: "grid",
                    placeItems: "center",
                    flexShrink: 0,
                    bgcolor: "background.neutral",
                    color: "text.tertiary",
                  }}
                >
                  <Iconify
                    icon={getEval(r.id)?.icon || "solar:shield-check-linear"}
                    width={14}
                  />
                </Box>
                <Typography
                  noWrap
                  sx={{ flex: 1, typography: "s2", fontWeight: 600 }}
                >
                  {r.name}
                </Typography>
                {resolved ? (
                  <ScorePill
                    score={r.score}
                    passed={r.passed}
                    label={r.reason}
                  />
                ) : (
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {task.status === "queued"
                      ? "—"
                      : task.status === "running"
                        ? "After the call"
                        : "Scoring…"}
                  </Typography>
                )}
              </Stack>
            );
          })}
        </Stack>
      )}

      {/* What a pass looks like — the line the evals grade against. */}
      <ColumnLabel>Passes when</ColumnLabel>
      <Stack spacing={1.25} sx={{ p: 2 }}>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          {(task.expected || "").replace(/\."\.$/, '".')}
        </Typography>
        {task.critical && (
          <Stack direction="row" spacing={0.75} alignItems="flex-start">
            <Iconify
              icon="solar:danger-triangle-bold"
              width={14}
              sx={{ color: "status.danger.fg", flexShrink: 0, mt: "2px" }}
            />
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              <b>Critical</b> · {shortReason(task.blockerReason)}
            </Typography>
          </Stack>
        )}
      </Stack>

      {/* Who is calling. The call header has the name; this has the rest. */}
      {task.persona && (
        <>
          <ColumnLabel>Caller</ColumnLabel>
          <Stack
            direction="row"
            spacing={1}
            alignItems="flex-start"
            sx={{ p: 2 }}
          >
            <Box
              sx={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                display: "grid",
                placeItems: "center",
                flexShrink: 0,
                bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
                color: "primary.main",
                typography: "s3",
                fontWeight: 600,
              }}
            >
              {task.persona.name
                .split(" ")
                .map((w) => w[0])
                .slice(0, 2)
                .join("")}
            </Box>
            <Box minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {task.persona.name}
                <Box
                  component="span"
                  sx={{ color: "text.tertiary", fontWeight: 400 }}
                >
                  {task.persona.age
                    ? ` · ${task.persona.age}`
                    : task.persona.role
                      ? ` · ${task.persona.role}`
                      : ""}
                </Box>
              </Typography>
              {/* Wraps — a trait cut off mid-word is a trait nobody reads. */}
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {[...(task.persona.traits || []), task.persona.voice]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>
          </Stack>
        </>
      )}
    </Box>
  );
}

/* The blocker reason without the rule quoted again — "Passes when" just said it. */
const shortReason = (reason) =>
  (reason || "marked critical — worth reading first.")
    .replace(/:\s*“.*”\.?$/, ".")
    .replace(/^./, (c) => c.toLowerCase());

/*
  Where each sub-goal stands, read from the conversation itself.

  A script tags the turn that meets each milestone (verify, recognise, act,
  close), so a sub-goal ticks on that line. One whose wording isn't familiar is
  spread across the call instead. A failing run fails at its crux: what was
  due there, and everything after it, is missed.
*/
function subGoalStates(task, env) {
  const raw = task.subTasks?.length ? task.subTasks : subTasksFor(task, env);
  const n = raw.length;
  const last = task.steps.length - 1;
  const turnFor = (milestone, k) => {
    const at = milestone
      ? task.steps.findIndex((st) => st.meets?.includes(milestone))
      : -1;
    return at >= 0 ? at : Math.floor(((k + 1) / n) * last);
  };
  const failAt = task.failStep;
  const reached = task.stepIndex;
  const done =
    ["passed", "failed", "flaky"].includes(task.status) ||
    task.status === "grading";
  const rows = raw.map((g, k) => {
    const label = typeof g === "string" ? g : g.label;
    const turn = turnFor(milestoneOf(label, k, n), k);
    let state = "open";
    if (task.status === "unmeasured") state = "unscored";
    else if (failAt != null && turn >= failAt && (reached >= failAt || done))
      state = "missed";
    else if (reached >= turn) state = "met";
    return {
      id: typeof g === "string" ? `${task.id}::${k}` : g.id,
      label,
      turn,
      state,
    };
  });
  /* The one the conversation is working towards now. */
  if (task.status === "running") {
    const next = rows
      .filter((r) => r.state === "open")
      .sort((a, b) => a.turn - b.turn)[0];
    if (next) next.state = "active";
  }
  return rows;
}

function SubGoalRow({ goal }) {
  const meta = {
    met: {
      icon: "solar:check-circle-bold",
      color: "status.success.fg",
      note: `turn ${goal.turn + 1}`,
    },
    missed: { icon: "solar:close-circle-bold", color: RED, note: "missed" },
    unscored: { note: "not measured" },
    active: { note: "in progress" },
    open: {},
  }[goal.state];
  return (
    <Stack direction="row" spacing={0.875} alignItems="flex-start">
      {meta.icon ? (
        <Iconify
          icon={meta.icon}
          width={14}
          sx={{ color: meta.color, flexShrink: 0, mt: "2px" }}
        />
      ) : (
        /* Not reached yet — a plain ring, drawn rather than fetched. The one
           being worked on now pulses in the accent. */
        <Box
          sx={{
            width: 12,
            height: 12,
            m: "3px 1px 0",
            borderRadius: "50%",
            flexShrink: 0,
            border: "1.5px solid",
            borderColor:
              goal.state === "active" ? "primary.main" : "text.disabled",
            ...(goal.state === "active" && {
              animation: `${pulse} 1.4s ease-in-out infinite`,
            }),
          }}
        />
      )}
      <Typography
        sx={{
          flex: 1,
          minWidth: 0,
          typography: "s3",
          color:
            goal.state === "open" || goal.state === "unscored"
              ? "text.tertiary"
              : "text.secondary",
          fontWeight: goal.state === "active" ? 600 : 400,
        }}
      >
        {goal.label}
      </Typography>
      {goal.basis && (
        <Tooltip arrow placement="left" title={goal.basis.label}>
          <Iconify
            icon={goal.basis.icon}
            width={12}
            sx={{ color: "text.disabled", flexShrink: 0, mt: "2px" }}
          />
        </Tooltip>
      )}
      {meta.note && (
        <Typography
          sx={{
            typography: "s3",
            flexShrink: 0,
            whiteSpace: "nowrap",
            color:
              goal.state === "missed"
                ? "status.danger.fg"
                : goal.state === "active"
                  ? "primary.main"
                  : "text.disabled",
          }}
        >
          {meta.note}
        </Typography>
      )}
    </Stack>
  );
}

function formatMs(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

ColumnLabel.propTypes = { children: PropTypes.node, sticky: PropTypes.bool };
Chip.propTypes = { icon: PropTypes.string, label: PropTypes.string };

OutcomeBar.propTypes = { stats: PropTypes.object.isRequired };

TaskRow.propTypes = {
  task: PropTypes.object,
  active: PropTypes.bool,
  stale: PropTypes.string,
  onClick: PropTypes.func,
};

SkippedRow.propTypes = { scenario: PropTypes.object };
SubGoalRow.propTypes = { goal: PropTypes.object };

LiveEvalPanel.propTypes = {
  grading: PropTypes.object,
  task: PropTypes.object,
  evals: PropTypes.array,
  env: PropTypes.object,
};
