import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import SideDrawer from "../../components/SideDrawer";
import { omegaReport, omegaVerdict, diagnosisTrace } from "../../_mock/omega";
import {
  proposalsFor,
  projectedRate,
  addressedCount,
  optimisable,
} from "../../_mock/optimize";
import { improvementRunName } from "../../_mock/optimizer";
import { shippedCandidate } from "./tradeoffs";
import { nextAgentVersion, currentEnvVersion } from "../../_mock/versions";
import { optimizationSummary, OPT_STATUS } from "../../_mock/optimizationRuns";
import { protoRunId } from "../../_mock/executionAdapter";
import DiagnosisPane from "./DiagnosisPane";
import ImprovementSetup from "./ImprovementSetup";
import {
  buildImprovement,
  budgetsWithin,
  envStateOnVersion,
  todaySpend,
} from "../../_mock/improvementStart";
import { measureTodayRun, measuredSummaryOf } from "../../_mock/measureToday";
import { rereadFor } from "../../_mock/improvementPolicy";
import { recordOutsideChange } from "../../_mock/nudgeEffects";
import { asCoderImprovement } from "../../_mock/improverAttempts";
import { startImproverSession } from "../../_mock/integrations";
import { limitsOf } from "../../agents/agentLimits";
import { envAgentKey } from "../../_mock/agentRegistry";
import OptimizationRunView from "./OptimizationRunView";
import { useSimStore } from "../../store";
import { improvementPath } from "../../improvements/improvementNav";

/* A change's diff with its added lines replaced by a person's wording; what
   it removes stays. */
const editedDiff = (diff = [], lines = []) => [
  ...diff.filter((d) => d.type !== "add"),
  ...lines.filter((t) => t.trim()).map((text) => ({ type: "add", text })),
];

/**
 * Debug failures → Improve.
 *
 * One drawer, two widths. Narrow while you are reading what went wrong, wide
 * once an improvement run is open — because a diagnosis is a column of text
 * and an improvement run is a graph, a grid of trials and a scenario matrix.
 *
 * The drawer itself is the shared `SideDrawer`, so the page behind stays
 * readable and it looks like every other drawer in the product.
 */

export default function FixMyAgentDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  addAgentVersion,
  tasks,
  picked = null,
  stats,
  runId,
  onOpenTask,
  onViewIssue,
  openOptimizationId,
}) {
  const navigate = useNavigate();
  const { state: simState, commit } = useSimStore();
  /* Opening a row in the runs list lands on that run, not on the diagnosis —
     the list already told you which one you wanted. */
  const [view, setView] = useState(
    openOptimizationId ? "optimization" : "diagnosis",
  );
  const [activeId, setActiveId] = useState(openOptimizationId || null);

  useEffect(() => {
    if (!open) return;
    setView(openOptimizationId ? "optimization" : "diagnosis");
    setActiveId(openOptimizationId || null);
  }, [open, openOptimizationId]);
  /*
    Every proposed change starts included — the improvement run acts on the
    whole diagnosis, not a hand-picked subset.
  */
  const [applied, setApplied] = useState({});

  const measured = useMemo(
    () => tasks.filter((t) => t.status !== "unmeasured"),
    [tasks],
  );
  const failing = useMemo(() => optimisable(tasks), [tasks]);

  const report = useMemo(
    () => omegaReport({ env, summary: { tasks }, baseline: null }),
    [env, tasks],
  );
  /* A person's wording for a proposed change, before the search runs:
     { [proposalId]: ["line", …] } — it replaces the change's added lines,
     here and in the search's pool, so the trials try that wording. */
  const [edits, setEdits] = useState({});
  useEffect(() => {
    if (open) setEdits({});
  }, [open]);
  const withEdits = (p) =>
    edits[p.id]
      ? { ...p, diff: editedDiff(p.diff, edits[p.id]), edited: true }
      : p;
  const proposals = useMemo(
    () => proposalsFor(env, failing, measured).map(withEdits),
    [env, failing, measured, edits], // eslint-disable-line react-hooks/exhaustive-deps
  );

  /*
    Seed `applied` with every proposal id, once per proposal — a `seenRef`
    keeps a later identity change from resetting the map.
  */
  const seenRef = useRef(new Set());
  useEffect(() => {
    setApplied((prev) => {
      let changed = false;
      const next = { ...prev };
      proposals.forEach((p) => {
        if (seenRef.current.has(p.id)) return;
        seenRef.current.add(p.id);
        next[p.id] = true;
        changed = true;
      });
      return changed ? next : prev;
    });
  }, [proposals]);
  const trace = useMemo(
    () => diagnosisTrace({ report, tasks, proposals, checks: [] }),
    [report, tasks, proposals],
  );

  const included = proposals.filter((p) => applied[p.id]);
  const projected = projectedRate(measured, included);
  const willFix = addressedCount(included);
  const current = Math.round((stats?.passRate || 0) * 100);

  const records = envState?.optimizations || [];
  const active = records.find((o) => o.id === activeId) || null;

  /* If the drawer opened from a trial page, `runId` is a synthetic trial id
     that doesn't exist in envState.runs — resolve it to the run the
     improvement run started from, so `fromRunId` always names a real run. */
  const trialM = /^(OPT-\d+)-t(\d+)$/.exec(runId || "");
  const sourceRunId = trialM
    ? records.find((o) => o.id === trialM[1])?.fromRunId || runId
    : runId;
  const sourceRun = (envState?.runs || []).find((r) => r.id === sourceRunId);
  /* The agent version the diagnosed run tested — what changes are made on. */
  const runAgentVersion = sourceRun?.agentVersion || null;
  /* And the agent it tested: a run keeps the agent it ran on (stamped when
     the environment swaps agents), so an old run never improves today's. */
  const envKey = envAgentKey(envState?.agent, env?.id);
  const runAgentKey = sourceRun?.agentKey || envKey;
  const swapped = !!runAgentKey && !!envKey && runAgentKey !== envKey;
  const allScenarioIds = (envState?.scenarios || []).map((sc) => sc.id);
  const runScenarioIds = sourceRun?.scenarioIds?.length
    ? sourceRun.scenarioIds
    : allScenarioIds;

  /* Earlier improvement runs, newest first — so a finished one can be opened
     again from the diagnosis instead of only from the moment it ran. */
  const previous = useMemo(
    () =>
      [...(envState?.optimizations || [])].reverse().map((o) => {
        const s = optimizationSummary(o);
        return {
          id: o.id,
          name: improvementRunName(envState, o),
          sub:
            o.status === OPT_STATUS.RUNNING
              ? "running"
              : s.sub
                ? `${s.headline} ${s.sub}`
                : "",
        };
      }),
    [envState],
  );

  /*
    Starting a run writes the record first and marks it running.

    The search is deterministic and returns instantly, but the record has to
    exist in a running state before the trace plays — otherwise closing the
    drawer mid-search would lose a run that, in a real system, is already
    burning episodes on somebody's infrastructure.
  */
  /*
    "Run self improvement" opens Improve my agent, already on this agent and
    this environment: name it, pick the optimizer and model, the goal and the
    budget. Starting writes the record running and opens the improvement's
    own page — the same page every improvement runs on, its runs arriving
    in the table as they finish. Saved before leaving (`commit`), so the
    page finds it.
  */
  const [setupOpen, setSetupOpen] = useState(false);
  const startFromSetup = ({
    name,
    model,
    writerModel,
    envIds,
    goal,
    budget,
    limits,
    seed,
    lineage,
    reread,
    textSearch,
    targetMember,
    optimizerId,
    base,
    today,
    depth,
    pick,
  }) => {
    /* An outside change re-read at Start is measured first, on that version
       — recorded as a run, and part of what the improvement spends. */
    if (today) commit({ type: "recordRun", envId: env.id, run: today });
    const record = buildImprovement({
      /* Edit plan's cases to fix and to protect, when the person changed them. */
      pick,
      reread,
      textSearch,
      targetMember,
      optimizerId,
      state: simState,
      env,
      es: today
        ? { ...envState, runs: [today, ...(envState?.runs || [])] }
        : envState,
      tasks: today && base?.tasks ? base.tasks : tasks,
      seed,
      fromRunId: today?.id || sourceRunId,
      baseAgentVersion: base?.agentVersion || runAgentVersion,
      spentFirst: today ? todaySpend(today, env.surface) : 0,
      trigger: {
        kind: "run",
        goal: goal
          ? {
              id: goal.id,
              label: goal.label,
              ...(goal.group && { group: goal.group }),
              ...(goal.reliability && { reliability: goal.reliability }),
            }
          : undefined,
      },
      limits,
      budget,
      regressionEnvIds: envIds,
      lineage,
      extra: {
        ...(name && { name }),
        ...(model && { model }),
        ...(writerModel && { writerModel }),
        includedIds: included.map((p) => p.id),
        /* Suggest only, chosen in the setup. */
        ...(depth && { fixDepth: depth, suggestChosen: true }),
        /* The diagnosis this attempt was made against, kept with it — six
           weeks on, "why did we try that" is only answerable if the evidence
           travelled with the attempt. */
        diagnosis: report
          .filter((a) => !a.nothing)
          .map((a) => ({
            id: a.id,
            label: a.label,
            headline: a.headline,
            severity: a.severity,
          })),
      },
    });
    /* Edited changes go into the search as written. */
    const edited = Object.keys(edits).length
      ? (() => {
          const pool = (r) =>
            r && { ...r, pool: (r.pool || []).map(withEdits) };
          return {
            ...record,
            proposalEdits: edits,
            ...(record.result && { result: pool(record.result) }),
            ...(record.pendingResult && {
              pendingResult: pool(record.pendingResult),
            }),
            ...(record.continueResult && {
              continueResult: pool(record.continueResult),
            }),
          };
        })()
      : record;
    commit({
      type: "patchEnvState",
      envId: env.id,
      patch: { optimizations: [...records, edited] },
    });
    setSetupOpen(false);
    const from = (envState?.runs || []).find((r) => r.id === sourceRunId);
    navigate(improvementPath(env.id, record.id), {
      state: {
        back: {
          /* Back lands where it's listed: the run's Improvements tab. */
          to: `${paths.dashboard.simulate.simulationRun(env.id, sourceRunId)}?tab=improvements`,
          label: from?.ordinal ? `Run ${from.ordinal}` : "the run",
        },
      },
    });
  };

  /*
    Handed to the customer's coding agent from here (Open in Claude Code,
    Dispatch to …): an improvement on this run's failures starts for it —
    no search of ours; the agent's attempts are the candidates and we judge
    each one. Not navigated to: the brief's confirmation links there.
  */
  const handToAgent = (coder) => {
    /* Never on an agent this environment no longer runs. */
    if (!env?.id || !envState || swapped) return null;
    const limits = limitsOf(simState, runAgentKey);
    /* Re-read first; an outside change is measured on its new version, so the
       baseline's version and scores match. */
    const agent = { ...envState.agent, key: envKey };
    const reread = rereadFor(agent, simState);
    recordOutsideChange(commit, simState, {
      agent,
      reread,
      activateEnvId: env.id,
    });
    const esOn = envStateOnVersion(envState, reread);
    const today =
      esOn !== envState
        ? measureTodayRun(env, esOn, {
            key: `coder-${env.id}-${Date.now().toString(36)}`,
            measuredFor: { kind: "run", runId: sourceRunId },
            startedBy: "Improvement · Debug failures",
          })
        : null;
    const base = today ? measuredSummaryOf(env, esOn, today) : null;
    if (today) commit({ type: "recordRun", envId: env.id, run: today });
    const record = asCoderImprovement(
      buildImprovement({
        state: simState,
        env,
        es: today
          ? { ...envState, runs: [today, ...(envState?.runs || [])] }
          : envState,
        tasks: base?.tasks || tasks,
        reread,
        fromRunId: today?.id || sourceRunId,
        baseAgentVersion: base?.agentVersion || runAgentVersion,
        spentFirst: today ? todaySpend(today, env.surface) : 0,
        trigger: { kind: "run" },
        limits,
        budget: budgetsWithin(limits.budget.dollars).at(-1),
        regressionEnvIds: [env.id],
        extra: {
          diagnosis: report
            .filter((a) => !a.nothing)
            .map((a) => ({
              id: a.id,
              label: a.label,
              headline: a.headline,
              severity: a.severity,
            })),
        },
      }),
      coder,
    );
    commit({
      type: "patchEnvState",
      envId: env.id,
      patch: { optimizations: [...records, record] },
    });
    startImproverSession({
      agentId: coder.id,
      envId: env.id,
      optId: record.id,
      where: "future-agi",
    });
    const from = (envState?.runs || []).find((r) => r.id === sourceRunId);
    return {
      href: improvementPath(env.id, record.id),
      state: {
        back: {
          /* Back lands where it's listed: the run's Improvements tab. */
          to: `${paths.dashboard.simulate.simulationRun(env.id, sourceRunId)}?tab=improvements`,
          label: from?.ordinal ? `Run ${from.ordinal}` : "the run",
        },
      },
      label: "See its attempts",
    };
  };

  const finish = () => {
    patch({
      optimizations: records.map((o) =>
        o.id === activeId
          ? {
              ...o,
              status: OPT_STATUS.COMPLETED,
              completedAt: new Date().toISOString(),
            }
          : o,
      ),
    });
  };

  /*
    A change landed — merged, pushed, or confirmed deployed. This is the only
    place a version is saved from here; a PR merge arrives with the version
    PrCard already saved. The projection the user saw is recorded beside it,
    with the env version and scenario set a run must match to answer it.
  */
  const land = ({
    key,
    version: minted,
    origin,
    note,
    changes,
    projected: expected,
    projectedOn,
    scenarioIds,
  }) => {
    /* A draft until it's released — environments following production
       never test a version that isn't live. */
    const version = minted || {
      ...nextAgentVersion(envState, {
        note,
        applied: changes,
        fromRunId: sourceRunId,
        basedOnVersion: runAgentVersion,
      }),
      origin,
      status: "draft",
    };
    if (!minted) addAgentVersion?.(version);
    const at = new Date().toISOString();
    patch({
      omegaExpectation: {
        key,
        version: version.label,
        fromRun: sourceRunId || null,
        projected: expected,
        projectedOn,
        envVersion: currentEnvVersion(env, envState)?.label || null,
        scenarios: scenarioIds,
        addresses: [...new Set(changes.flatMap((c) => c.addresses || []))],
        at,
      },
      /* Which hand-off landed as which version — so its button can't save a
         second one, whatever the expectation slot holds later. */
      handoffLandings: {
        ...(envState?.handoffLandings || {}),
        [key]: version.label,
      },
    });
  };

  /* Re-run the landed version by name — landing doesn't change what the
     environment tests. Without one, the version the run tested. It never mints. */
  const rerun = (scenarioIds, agent = null) => {
    const url = paths.dashboard.simulate.simulationRun(
      env.id,
      protoRunId(env.id, Date.now().toString(36)),
    );
    const subset =
      scenarioIds?.length && scenarioIds.length < allScenarioIds.length;
    const q = new URLSearchParams();
    if (subset) q.set("only", scenarioIds.join(","));
    /* The run's version names the run's agent; once the environment swapped
       agents, that label would pick the new agent's version instead. */
    const version = agent || (swapped ? null : runAgentVersion);
    if (version) q.set("agent", version);
    onClose();
    navigate(q.toString() ? `${url}?${q.toString()}` : url);
  };

  /* The winner on every scenario at the current env version — runs only
     saw the target scenarios. Tagged on the record so the runs list can name
     it, and a chosen run counts as verified once it has run. */
  const runWinner = (record, candidate = null) => {
    const id = protoRunId(env.id, Date.now().toString(36));
    patch({
      optimizations: records.map((o) =>
        o.id === record.id
          ? {
              ...o,
              verifyRunIds: [...(o.verifyRunIds || []), id],
              verifiedTrial: o.preferredTrial || o.result?.winner?.n,
            }
          : o,
      ),
    });
    onClose();
    /* The winner applied to the version the search started from, named —
       never whatever the environment tests today. From a code snapshot
       when one was picked; the run says so. */
    const url = paths.dashboard.simulate.simulationRun(env.id, id);
    const q = new URLSearchParams();
    if (record.baseAgentVersion) q.set("agent", record.baseAgentVersion);
    if (candidate) q.set("candidate", candidate);
    navigate(q.toString() ? `${url}?${q.toString()}` : url);
  };

  const provenance = {
    fromRunId: sourceRunId || null,
    basedOnVersion: runAgentVersion,
  };
  const diagKey = `diag:${sourceRunId}`;
  const landedFor = (key) => envState?.handoffLandings?.[key] || null;

  const close = () => {
    setView("diagnosis");
    setActiveId(null);
    onClose();
  };

  return (
    <SideDrawer
      open={open}
      onClose={close}
      /*
        Wide enough for the widest thing it holds, and no wider — the
        scenario grid, at roughly 950px, sets the real requirement.
      */
      width={
        view === "optimization"
          ? { xs: "100%", md: 960 }
          : { xs: "100%", sm: 640 }
      }
    >
      {view === "diagnosis" && (
        <DiagnosisPane
          tasks={tasks}
          picked={picked}
          onEditChange={(id, lines) =>
            setEdits((prev) => {
              const next = { ...prev };
              if (lines) next[id] = lines;
              else delete next[id];
              return next;
            })
          }
          report={report}
          trace={trace}
          proposals={proposals}
          verdict={omegaVerdict(report, failing)}
          applied={applied}
          setApplied={setApplied}
          current={current}
          projected={projected}
          willFix={willFix}
          measured={measured}
          failing={failing}
          onOpenTask={onOpenTask}
          onViewIssue={onViewIssue}
          runId={runId}
          onOptimize={() => setSetupOpen(true)}
          onHandToAgent={handToAgent}
          previous={previous}
          onOpenPrevious={(id) => {
            setActiveId(id);
            setView("optimization");
          }}
          handoff={{
            key: diagKey,
            landedVersion: landedFor(diagKey),
            provenance,
            onLand: (info, changes, expected) =>
              land({
                ...info,
                key: diagKey,
                changes,
                projected: expected,
                projectedOn: "if every addressed scenario passes",
                scenarioIds: runScenarioIds,
              }),
            onRerun: (agent) => rerun(runScenarioIds, agent),
          }}
          env={env}
          envState={envState}
          patch={patch}
          onClose={close}
        />
      )}

      {view === "optimization" && active && (
        <OptimizationRunView
          record={active}
          name={improvementRunName(envState, active)}
          env={env}
          envState={envState}
          patch={patch}
          tasks={tasks}
          onOpenTask={onOpenTask}
          onRunWinner={(candidate) => runWinner(active, candidate)}
          handoff={{
            key: active.id,
            landedVersion: landedFor(active.id),
            provenance,
            /* The held-out score is the number the screen showed, so it is
               the projection — and the full suite is what answers it. */
            onLand: (info) =>
              land({
                ...info,
                key: active.id,
                changes:
                  shippedCandidate(active)?.proposals ||
                  active.result?.winner?.proposals ||
                  [],
                projected:
                  shippedCandidate(active)?.held ?? active.result?.heldScore,
                projectedOn: "held-back scenarios",
                scenarioIds: allScenarioIds,
              }),
            onRerun: (agent) => rerun(allScenarioIds, agent),
          }}
          onBack={() => {
            setView("diagnosis");
            setActiveId(null);
          }}
          onClose={close}
          onDone={finish}
        />
      )}
      <ImprovementSetup
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        preset={{
          agentKey: runAgentKey,
          envId: env?.id,
          runId: sourceRunId || undefined,
          agentVersion: runAgentVersion || undefined,
          envVersion: sourceRun?.envVersion || undefined,
        }}
        tasks={tasks}
        included={included}
        onStart={startFromSetup}
      />
    </SideDrawer>
  );
}

FixMyAgentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  addAgentVersion: PropTypes.func,
  tasks: PropTypes.array,
  /* Debug selected: { count, leftOut } — only those failures are in tasks. */
  picked: PropTypes.object,
  stats: PropTypes.object,
  runId: PropTypes.string,
  onOpenTask: PropTypes.func,
  onViewIssue: PropTypes.func,
  openOptimizationId: PropTypes.string,
};
