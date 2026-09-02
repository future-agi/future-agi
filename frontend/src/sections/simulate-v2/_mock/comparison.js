/**
 * Runs, side by side.
 *
 * A single run answers "how did this agent do". The question people actually
 * have is "is it better than it was", and that only has an answer because the
 * scenarios belong to the environment rather than the agent: nothing was
 * rewritten between run A and run B, so every row that moved is attributable
 * to the agent version.
 *
 * Everything here is rebuilt rather than stored. `buildRun` is deterministic in
 * its seed and the run player seeds it with the run id, so replaying a run from
 * history produces exactly the numbers it produced live — which is what lets a
 * comparison reach back to a run nobody kept the results of.
 */

import { buildRun } from "./runStream";
import { resolveEval } from "./evals";
import { getSurface } from "./surfaces";
import { versionNumber } from "./versions";
import { attribute, domainTally, isMeasured, faultReason } from "./failures";
import { episodeReturn } from "./reward";
import { checklistSteps } from "./callDetail";

/** A, B, C… — the label a run is referred to by once it is in a comparison. */
export const runLetter = (i) => String.fromCharCode(65 + (i % 26));

/**
 * Colours for run identity.
 *
 * Indexed by the run's place in the sequence rather than by the order someone
 * happened to tick it, so a run is the same letter and the same colour on the
 * summary, in a comparison and in the drawer. A label that changes depending on
 * how you arrived at the screen is not an identity.
 */
export const RUN_COLORS = [
  "#7857FC", "#2563EB", "#16A34A", "#CA8A04",
  "#EA580C", "#DB2777", "#0D9488", "#4F46E5",
];

const evalsOf = (envState) => (envState?.evals || []).map(resolveEval).filter(Boolean);

/**
 * The scenarios a stored run actually covered.
 *
 * A run is not automatically a run of everything: someone re-runs three
 * blockers, or the set grew after the run finished. The run carries its own
 * ids, and anything since deleted simply cannot be rebuilt — reported as
 * `dropped` rather than quietly skipped.
 */
export const runScenarios = (envState, run) => {
  const all = envState?.scenarios || [];
  if (!run?.scenarioIds?.length) return { scenarios: all, dropped: 0 };
  const byId = new Map(all.map((sc) => [sc.id, sc]));
  const scenarios = run.scenarioIds.map((id) => byId.get(id)).filter(Boolean);
  return { scenarios, dropped: run.scenarioIds.length - scenarios.length };
};

/** The tasks a stored run produced, rebuilt from its id. */
export const rebuildRun = (env, envState, run) =>
  buildRun({
    seed: run.id,
    scenarios: runScenarios(envState, run).scenarios,
    stage: getSurface(env?.surface).stage,
    evals: evalsOf(envState),
    tools: env?.tools || [],
    repeats: run.repeats || 1,
    phrasing: versionNumber(run.agentVersion),
  }).tasks.map((t) => ({ ...t, status: t.verdict }));

/**
 * One row of the summary table.
 *
 * Counted off the rebuilt tasks rather than the numbers stored with the run, so
 * the table, the chart and the comparison can never disagree with each other.
 */
export const runSummary = (env, envState, run, index) => {
  const tasks = rebuildRun(env, envState, run);
  const { dropped } = runScenarios(envState, run);
  const passed = tasks.filter((t) => t.status === "passed").length;
  const flaky = tasks.filter((t) => t.status === "flaky").length;
  /*
    Only scenarios that produced a verdict count towards the agent. A run whose
    sandbox fell over on three rows did not score 60% — it scored what it scored
    on the fourteen it could measure, and lost three.
  */
  const measured = tasks.filter(isMeasured);
  const unmeasured = tasks.length - measured.length;
  const evalIds = evalsOf(envState).map((e) => e.id);

  const scores = {};
  evalIds.forEach((id) => {
    const vals = tasks
      .map((t) => t.evalResults?.find((r) => r.id === id))
      .filter(Boolean);
    scores[id] = vals.length
      ? Math.round((vals.filter((v) => v.passed).length / vals.length) * 100)
      : null;
  });

  return {
    ...run,
    index,
    /* Its number, as stamped when it ran. */
    ordinal: run.ordinal || index + 1,
    letter: runLetter(index),
    color: RUN_COLORS[index % RUN_COLORS.length],
    /* Pinned when the run started, not inferred from its place in the list.
       Two runs of one unchanged agent are a real thing to want — it is how you
       find out a scenario is flaky rather than the agent worse. */
    agentVersion: run.agentVersion || `v${index + 1}`,
    /* And the env version — same reasoning. A comparison across env
       versions with a fixed agent isolates world drift; a comparison
       across agent versions with a fixed env isolates the agent.

       Runs recorded before this field existed fall back to the newest
       env version at read time — not perfect (a very old run against a
       long-gone world will show whatever's newest today), but better
       than a blank pill that reads as "env unknown" on every seed row.
       If the row has its own stamp, we trust it. */
    envVersion: run.envVersion || envState?.envVersions?.slice(-1)[0]?.label || "v3",
    tasks,
    total: tasks.length,
    passed,
    flaky,
    dropped,
    repeats: run.repeats || 1,
    /*
      The mean of each scenario's pass proportion, not the share of scenarios
      that passed every time. With n samples a scenario that passes two of
      three is two thirds of a pass, and rounding it down to a failure throws
      away the only evidence anyone has that the number moves on its own.
    */
    measured: measured.length,
    unmeasured,
    /* What the environment paid out, not just how many checks passed. The
       whole claim of an RL environment is that it produces a return; a run
       that never reports one is a test harness wearing the name. */
    meanReturn: (() => {
      const returns = measured
        .map((t) => episodeReturn(t, { steps: checklistSteps(t, env, t) })?.total)
        .filter((v) => v != null);
      return returns.length
        ? Math.round((returns.reduce((a, v) => a + v, 0) / returns.length) * 100) / 100
        : null;
    })(),
    /* What went wrong, grouped by whose fault it was. */
    domains: domainTally(tasks),
    passRate: measured.length
      ? Math.round((measured.reduce((a, t) => a + (t.passShare ?? (t.status === "passed" ? 1 : 0)), 0) / measured.length) * 100)
      : 0,
    avgDurationMs: tasks.length
      ? Math.round(tasks.reduce((a, t) => a + (t.durationMs || 0), 0) / tasks.length)
      : 0,
    tokens: tasks.reduce((a, t) => a + (t.tokens || 0), 0),
    cost: tasks.reduce((a, t) => a + (t.cost || 0), 0),
    /* Claims the call log did not support — the failure a transcript hides. */
    saidNotDone: tasks.filter((t) => t.callLog?.unsupportedClaim).length,
    scores,
  };
};

/** Every run this environment has, oldest first, so the chart reads left to right. */
export const runSummaries = (env, envState) => {
  const ordered = [...(envState?.runs || [])].sort(
    (a, b) => new Date(a.finishedAt) - new Date(b.finishedAt),
  );
  return ordered.map((r, i) => runSummary(env, envState, r, i));
};

/** One series per eval across the runs — the trend above the table. */
export const evalSeries = (summaries, envState) =>
  evalsOf(envState).map((e) => ({
    id: e.id,
    name: e.name,
    color: e.color,
    data: summaries.map((s) => (s.scores[e.id] == null ? null : s.scores[e.id])),
  }));

/* ── comparison ──────────────────────────────────────────────────────────── */

/**
 * Why a task ended the way it did, in one line.
 *
 * Ranked rather than concatenated. A claim the call log cannot support outranks
 * a failed grader, because the grader read words and the log read the world —
 * and when they disagree the log is the one that found something.
 */
export const decidingMoment = (task) => {
  if (!task) return { kind: "missing", text: "This scenario did not run here." };

  /* Attribution first. Everything below reads the agent's behaviour, and none
     of it applies when the agent never got a fair run. */
  const domain = attribute(task);
  if (domain && !domain.measured) {
    return { kind: "unmeasured", text: `${faultReason(task)} Not counted against the agent.` };
  }

  const failed = (task.evalResults || []).find((r) => !r.passed);

  /* A task that passed did not have a deciding failure, so reporting its worst
     grader as the decider states the opposite of the verdict beside it. What is
     worth saying about a pass is whether anything came close. */
  if (task.status === "passed") {
    if (failed) {
      return {
        kind: "near",
        text: `Passed, but ${failed.name} came in under its threshold — ${failed.reason}`,
      };
    }
    return {
      kind: "clean",
      text: "Every check passed and the call log matches what the agent said.",
    };
  }

  /* Samples that disagree with each other say something about the scenario,
     not the agent — and reading one sample's failure as the verdict is exactly
     the mistake the repeats exist to prevent. */
  if (task.status === "flaky") {
    return {
      kind: "flaky",
      text: `Passed ${task.passes} of ${task.repeats} samples — this scenario does not decide the same way twice.`,
    };
  }

  if (task.callLog?.unsupportedClaim) {
    return { kind: "claim", text: task.callLog.unsupportedClaim };
  }
  if (failed) {
    return { kind: "eval", text: `${failed.name} failed — ${failed.reason}` };
  }
  const missing = task.callLog?.missing?.[0];
  if (missing) {
    return { kind: "tool", text: `Never called ${missing}, which this scenario needed.` };
  }
  return { kind: "unclear", text: "Failed without a grader or a tool call explaining why." };
};

const pct = (now, before) => {
  if (!before) return null;
  return Math.round(((now - before) / before) * 100);
};

/**
 * The comparison itself: the chosen runs, and one row per scenario carrying
 * each run's verdict, its deciding moment and how far it moved from the
 * baseline. The first run selected is the baseline — everything else is read
 * as a change from it.
 */
export const buildComparison = (env, envState, runIds) => {
  const all = runSummaries(env, envState);
  /* Letters and colours travel with the run, so B here is the B in the table. */
  const runs = runIds.map((id) => all.find((r) => r.id === id)).filter(Boolean);

  if (runs.length === 0) return { runs: [], rows: [], evals: [], coverage: null };

  const baseline = runs[0];

  /*
    Rows are the union of what the runs covered, not the baseline's set.
    Intersecting would hide scenarios the newer run added; taking the baseline
    alone would silently drop them. Where a run did not cover a row it says so,
    which is the difference between "this got worse" and "this was not tested".
  */
  const seen = new Map();
  runs.forEach((run) => run.tasks.forEach((t) => { if (!seen.has(t.id)) seen.set(t.id, t); }));
  const scenarios = [...seen.values()];

  const rows = scenarios.map((base) => {
    const baseTask = baseline.tasks.find((t) => t.id === base.id);

    const cells = runs.map((run) => {
      const task = run.tasks.find((t) => t.id === base.id);
      /* An unmeasured cell has no share — not a zero one. Treating it as zero
         is how a dropped session becomes a regression in the next review. */
      const share = task && isMeasured(task)
        ? task.passShare ?? (task.status === "passed" ? 1 : 0)
        : null;
      const baseShare = baseTask && isMeasured(baseTask)
        ? baseTask.passShare ?? (baseTask.status === "passed" ? 1 : 0)
        : null;
      /*
        One sample flipping is inside the noise of a sampled counterpart. A
        change only counts as movement when it is larger than a single sample
        can account for — otherwise every screen above this one turns run-to-run
        jitter into a regression someone gets paged about.
      */
      const step = 1 / Math.max(1, task?.repeats || run.repeats || 1);
      const delta = share == null || baseShare == null ? null : share - baseShare;
      return {
        runId: run.id,
        letter: run.letter,
        color: run.color,
        task,
        status: task?.status || "missing",
        domain: task ? attribute(task) : null,
        measured: task ? isMeasured(task) : false,
        passes: task?.passes ?? null,
        repeats: task?.repeats ?? null,
        passShare: share,
        deciding: decidingMoment(task),
        durationMs: task?.durationMs || 0,
        tokens: task?.tokens || 0,
        cost: task?.cost || 0,
        /* Deltas are against the baseline, and the baseline has none. Neither
           has a cell whose run never touched this scenario — there is no
           number to subtract, and subtracting anyway printed NaN%. */
        shareDelta: run.id === baseline.id ? null : delta,
        significant: run.id === baseline.id ? false : delta != null && Math.abs(delta) > step + 1e-9,
        durationDelta: run.id === baseline.id || !task || !baseTask ? null : pct(task.durationMs, baseTask.durationMs),
        tokensDelta: run.id === baseline.id || !task || !baseTask ? null : pct(task.tokens, baseTask.tokens),
      };
    });

    /* Only runs that actually ran this scenario *and* measured it get a vote on
       whether it changed. "Not run" and "not measured" are both absences of
       evidence, and an absence cannot be a regression. */
    const voting = cells.filter((c) => c.status !== "missing" && c.measured);
    const verdicts = new Set(voting.map((c) => c.status));
    const moved = cells.some((c) => c.significant);
    return {
      id: base.id,
      title: base.title,
      task: base.task,
      persona: base.persona,
      critical: base.critical,
      cells,
      /* A row where every run agrees is not where anyone should be looking. */
      changed: verdicts.size > 1,
      /* …and a row that moved by less than one sample is agreement in disguise. */
      significant: moved,
      flaky: cells.some((c) => c.status === "flaky"),
      missing: cells.some((c) => c.status === "missing"),
      unmeasured: cells.some((c) => c.task && !c.measured),
      fixed: moved && cells[0]?.measured && cells[0]?.status !== "passed"
        && cells.slice(1).some((c) => c.measured && c.status === "passed"),
      broke: moved && cells[0]?.measured && cells[0]?.status === "passed"
        && cells.slice(1).some((c) => c.measured && c.status === "failed"),
    };
  });

  /*
    Whether these runs are actually comparable. The screen's whole claim is
    "same scenarios, so the movement is the agent" — it has to stop making that
    claim the moment it stops being true.
  */
  const shared = rows.filter((r) => !r.missing).length;
  const coverage = {
    total: rows.length,
    shared,
    partial: rows.length - shared,
    /* Rows where at least one run produced no verdict at all. */
    unmeasured: rows.filter((r) => r.unmeasured).length,
    /* n differs between runs → a proportion from one is not the same
       measurement as a proportion from the other. Worth saying out loud. */
    repeats: [...new Set(runs.map((r) => r.repeats || 1))],
  };

  return { runs, baseline, rows, evals: evalsOf(envState), coverage };
};

/**
 * Score distribution for one grader, in ten bands, one series per run.
 *
 * The table says which scenarios moved. This says whether a run got better
 * everywhere or simply stopped failing catastrophically on three of them.
 */
export const distributionFor = (comparison, evalId) => {
  const bands = Array.from({ length: 10 }, (_, i) => ({
    label: `${i * 10}–${(i + 1) * 10}%`,
    counts: comparison.runs.map(() => 0),
  }));

  comparison.runs.forEach((run, ri) => {
    run.tasks.forEach((t) => {
      const score = t.evalResults?.find((r) => r.id === evalId)?.score;
      if (typeof score !== "number") return;
      bands[Math.min(9, Math.floor(score * 10))].counts[ri] += 1;
    });
  });

  return bands;
};

/** Rows worth reading first: the ones where the runs disagree. */
export const changedCount = (comparison) => comparison.rows.filter((r) => r.changed).length;
