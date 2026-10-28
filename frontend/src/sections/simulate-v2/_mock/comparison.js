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
import { withAskSkipped } from "./clarify";
import { withRegrade } from "./regrade";
import { resolveEval } from "./evals";
import { getSurface } from "./surfaces";
import { runEnvVersion, runInputs } from "./toolFit";
import { attribute, domainTally, isMeasured, faultReason } from "./failures";
import { episodeReturn } from "./reward";
import { checklistSteps } from "./callDetail";
import { ensureProvenance } from "./scenarioProvenance";
import { pinnedScenarios } from "./decoupled";
import { improvementRunName } from "./optimizer";
import { scenarioTitle } from "./scenarios";

/**
 * Colours for run identity.
 *
 * Indexed by the run's place in the sequence rather than by the order someone
 * happened to tick it, so a run is the same letter and the same colour on the
 * summary, in a comparison and in the drawer. A label that changes depending on
 * how you arrived at the screen is not an identity.
 */
export const RUN_COLORS = [
  "#7857FC",
  "#2563EB",
  "#16A34A",
  "#CA8A04",
  "#EA580C",
  "#DB2777",
  "#0D9488",
  "#4F46E5",
];

const evalsOf = (envState) =>
  (envState?.evals || []).map(resolveEval).filter(Boolean);

/**
 * The scenarios a stored run actually covered.
 *
 * A run is not automatically a run of everything: someone re-runs three
 * blockers, or the set grew after the run finished. The run carries its own
 * ids, and anything since deleted simply cannot be rebuilt — reported as
 * `dropped` rather than quietly skipped.
 */
export const runScenarios = (envState, run) => {
  /* Each at the version the run pinned (§4.10.8): an edit since — a major
     bump to its caller instruction, say — doesn't change what an old run
     shows. Runs without pins replay today's rows, as they always did. */
  /* A fix's first run also carries the issue's scenarios — the fix's own,
     not the environment's, so they ride on the run (`fixScenarios`). */
  const all = pinnedScenarios(
    [...(envState?.scenarios || []), ...(run?.fixScenarios || [])],
    run,
  );
  if (!run?.scenarioIds?.length) return { scenarios: all, dropped: 0 };
  const byId = new Map(all.map((sc) => [sc.id, sc]));
  /* Rows of the removed cheat checks aren't "dropped" — they're gone by design. */
  const ids = run.scenarioIds.filter((id) => !/-canary-\d+$/.test(String(id)));
  const scenarios = ids.map((id) => byId.get(id)).filter(Boolean);
  return { scenarios, dropped: ids.length - scenarios.length };
};

/** The tasks a stored run produced, rebuilt from its id and the pairing it
 *  pinned: the agent version (how well it does, which tools it calls), the
 *  environment version (which tools the world answers) and the graders that
 *  were applied when it ran. */
export const rebuildRun = (env, envState, run) => {
  const inputs = runInputs(env, envState, {
    agent: run.agentVersion,
    envVersion: runEnvVersion(env, envState, run),
    actors: run.actors,
    actorEdits: run.actorEdits || null,
    toolFaults: run.toolFaults || null,
    /* Runs from before the extra admission gates replay under the first three. */
    admissionGates: run.admissionGates || 1,
    /* The updated RSI PRD's run rules replay only on runs that pinned them. */
    outcomes: run.outcomes || 0,
    requiredEvals: run.requiredEvals || 0,
    /* Sub-goals deciding, and the turn limit, replay only on runs that pinned them. */
    gradingRule: run.gradingRule || 0,
    turnLimits: run.turnLimits || 0,
    turnLimit: run.turnLimit,
  });
  /* A snapshot re-run replays its source's draws; anything else draws from
     its own id (not `run.seed` — seeded runs carry numeric seeds that differ
     from their ids, and their results come from the id). */
  const seed = run.replaySeed || run.id;
  const built = buildRun({
    seed,
    scenarios: runScenarios(envState, run).scenarios,
    stage: getSurface(env?.surface).stage,
    evals: (run.evals || envState?.evals || [])
      .map(resolveEval)
      .filter(Boolean),
    repeats: run.repeats || 1,
    ...inputs,
  }).tasks;
  /* A fix's first run skipped its one clarifying question (useRunPlayer). */
  const tasks =
    run.measuredFor?.kind === "fix" ? withAskSkipped(built, seed) : built;
  return withRegrade(
    tasks /* Scenarios the run's spending limit kept from starting never ran. */
      .filter((t) => !notStartedOf(run).has(t.id))
      .map((t) => ({ ...t, status: t.verdict })),
    run,
  );
};

const notStartedOf = (run) => new Set(run?.budget?.notStarted || []);

/**
 * One row of the summary table.
 *
 * Counted off the rebuilt tasks rather than the numbers stored with the run, so
 * the table, the chart and the comparison can never disagree with each other.
 */
export const runSummary = (env, envState, run, index) => {
  /* A fix's first run also played the issue's scenarios; the environment's
     history counts only its own. */
  const tasks = rebuildRun(env, envState, run).filter((t) => !t.fixScenario);
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
    /* Its number, as stamped when it ran. The chip letter and colour are keyed
       off this ordinal — not the list position — so the badge always matches
       the "Run N" label and stays the same on the list, the detail header and
       Compare. (Previously letter used the index and colour was reassigned by a
       hash in chipIdentity, so the same run showed different numbers/colours
       depending on which screen you reached it from.) */
    ordinal: run.ordinal || index + 1,
    letter: String(run.ordinal || index + 1),
    color: RUN_COLORS[((run.ordinal || index + 1) - 1) % RUN_COLORS.length],
    /* Pinned when the run started, not inferred from its place in the list.
       Two runs of one unchanged agent are a real thing to want — it is how you
       find out a scenario is flaky rather than the agent worse. */
    agentVersion: run.agentVersion || `v${index + 1}`,
    /* And the env version — same reasoning. A comparison across env
       versions with a fixed agent isolates world drift; a comparison
       across agent versions with a fixed env isolates the agent.

       Runs recorded before this field existed predate every version
       minted in the app, so they fall back to the newest version that was
       not minted here (the seeded history) — never to one rebuilt later,
       which would claim an old run was taken on a world that did not
       exist yet. If the row has its own stamp, we trust it. */
    envVersion: runEnvVersion(env, envState, run),
    toolGap: run.toolGap || [],
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
        .map(
          (t) => episodeReturn(t, { steps: checklistSteps(t, env, t) })?.total,
        )
        .filter((v) => v != null);
      return returns.length
        ? Math.round(
            (returns.reduce((a, v) => a + v, 0) / returns.length) * 100,
          ) / 100
        : null;
    })(),
    /* What went wrong, grouped by whose fault it was. */
    domains: domainTally(tasks),
    passRate: measured.length
      ? Math.round(
          (measured.reduce(
            (a, t) => a + (t.passShare ?? (t.status === "passed" ? 1 : 0)),
            0,
          ) /
            measured.length) *
            100,
        )
      : 0,
    avgDurationMs: tasks.length
      ? Math.round(
          tasks.reduce((a, t) => a + (t.durationMs || 0), 0) / tasks.length,
        )
      : 0,
    tokens: tasks.reduce((a, t) => a + (t.tokens || 0), 0),
    /* Only costs the agent's side reported; unverified ones are counted, never summed. */
    cost: tasks
      .filter((t) => !t.costUnverified)
      .reduce((a, t) => a + (t.cost || 0), 0),
    costUnverified: tasks.filter((t) => t.costUnverified && isMeasured(t))
      .length,
    /* Claims the call log did not support — the failure a transcript hides. */
    saidNotDone: tasks.filter((t) => t.callLog?.unsupportedClaim).length,
    scores,
  };
};

/** Every run this environment has, oldest first, so the chart reads left to
 *  right. The build-and-fit-check row IS run #1 — the env stands up by running
 *  its own scenarios end-to-end during construction — so it's included here
 *  too, matching the runs count on the rail, the environments list and the Runs
 *  tab. (It used to be filtered out, which left a freshly-built env showing 1
 *  run on those surfaces but "0 runs" in the Simulations summary.) */
/* Sort key that tolerates an in-progress run (no finishedAt yet) by falling
   back to startedAt, so a running run doesn't produce NaN comparisons that
   scramble ordinals/chip numbers. */
const runTime = (r) => {
  const t = Date.parse(r?.finishedAt || r?.startedAt || "");
  return Number.isNaN(t) ? 0 : t;
};

export const runSummaries = (env, envState) => {
  const ordered = [...(envState?.runs || [])].sort(
    (a, b) => runTime(a) - runTime(b),
  );
  return ordered.map((r, i) => runSummary(env, envState, r, i));
};

/**
 * Trials, rendered as first-class runs.
 *
 * A trial IS a simulation run — the search scored it by executing the
 * candidate prompt against the environment. Surfacing trials in the runs
 * list gives users what they already earned (a full history of every
 * execution) and lets Compare reach into a self improvement rather than
 * forcing them to eyeball the trials chart.
 *
 * The parent still exists as a first-class object — trials are grouped
 * under it in the table — so the self improvement concept is not weakened
 * by exposing the trials.
 */
const SI_TRIAL_COLORS = [
  "#7857FC",
  "#0D9488",
  "#CA8A04",
  "#DB2777",
  "#2563EB",
  "#16A34A",
  "#EA580C",
  "#4F46E5",
];

/* What a self-improvement trial did to a scenario that changes its outcome.
   "same" and "still-failing" leave the source run's own outcome in place. */
export const TRIAL_CHANGES = {
  fixed: "passed",
  passed: "passed",
  broke: "failed",
};

/* A small stable hash, so a run's generated numbers are the same every read. */
const hashOf = (str) => {
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1)
    h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
};

export const trialSummaries = (env, envState) => {
  const opts = envState?.optimizations || [];
  if (!opts.length) return [];
  const evalIds = evalsOf(envState).map((e) => e.id);
  const results = [];

  opts.forEach((opt, optIdx) => {
    if (!opt?.result?.trials?.length) return;
    const sourceRun = (envState?.runs || []).find(
      (r) => r.id === opt.fromRunId,
    );
    /* What the run it started from scored — each run's graders, return and
       "said, not done" move from there by what that run changed. */
    const source = sourceRun ? runSummary(env, envState, sourceRun, 0) : null;
    const baseFinishedAt = new Date(
      opt.completedAt || opt.createdAt || Date.now(),
    ).getTime();
    const winnerN = opt.result.winner?.n;
    const groupOrdinal = optIdx + 1;
    const scenarioCount =
      envState?.scenarios?.length ||
      sourceRun?.total ||
      Object.keys(opt.result.trials[0]?.perScenario || {}).length ||
      0;

    /*
      Each trial row reports the trial's OWN score — not a running max.
      A per-row monotone value attributed a score to a trial that wasn't
      that trial's, so the same trial read one number here and a different
      number in the optimizer view / when opened. The "self improvement
      only goes up" story lives on the best-so-far chart, which is the
      right place for a running max; a per-trial row has to be honest
      about that trial.
    */
    opt.result.trials.forEach((trial, tIdx) => {
      const passRate = Math.round(trial.score);
      /* Trials do not carry token / duration numbers of their own — the
         mock runs the search analytically. The row still needs numbers,
         because a hyphen in every system column reads as "broken row"
         and the CEO's whole point is that a trial is a real run. So we
         scale the source run's numbers by a seeded factor per trial —
         internally consistent, honest at a glance, and never claiming a
         precision the mock does not have. */
      const jitter = 0.88 + ((trial.n * 37 + optIdx * 13) % 25) / 100;
      const lift = source ? passRate - (source.passRate ?? passRate) : 0;
      const wobble = (key, span) =>
        (hashOf(`${opt.id}:${trial.n}:${key}`) - 0.5) * span;
      const scores = Object.fromEntries(
        evalIds.map((id) => {
          const from = source?.scores?.[id] ?? passRate;
          const pull = 0.6 + hashOf(`${opt.id}:${id}`) * 0.6;
          /* A gain closes part of the gap to 100, a loss part of the way to 0 —
           a grader already near the top can't jump past it. */
          const room = lift >= 0 ? (100 - from) / 100 : from / 100;
          return [
            id,
            Math.max(
              0,
              Math.min(
                100,
                Math.round(
                  from +
                    lift * pull * Math.max(0.25, room) * 1.6 +
                    wobble(id, 6),
                ),
              ),
            ),
          ];
        }),
      );
      const finishedAt = new Date(baseFinishedAt + tIdx * 60_000).toISOString();
      /* A trial is scored on the training split only — the held-out
         scenarios are what the winner is checked against afterwards. So a
         trial row covers those scenarios, and says so (partial), rather than
         reading as a full sweep of the environment. */
      const trialIds = Object.keys(trial.perScenario || {});
      const covered = trialIds.length || scenarioCount;
      const passed = Math.round((passRate / 100) * covered);

      const baseVersion =
        sourceRun?.agentVersion || opt.baseAgentVersion || null;
      results.push({
        id: `${opt.id}-t${trial.n}`,
        kind: "trial",
        /* A trial ran a candidate on top of the version it started from, so
           it's named as both — "agent v2" alone read as v2 itself. */
        trialLabel: `Trial ${trial.n} · ${baseVersion ? `${baseVersion} + change` : "change"}`,
        selfImprovementId: opt.id,
        /* The improvement's own name — a custom one when it was given, the same
           helper its page title reads. */
        selfImprovementName: improvementRunName(envState, opt),
        selfImprovementOrdinal: groupOrdinal,
        trialN: trial.n,
        trialTried: trial.tried,
        trialDelta: trial.delta,
        trialCount: opt.result.trials.length,
        winnerTrialN: winnerN,
        isWinner: trial.n === winnerN,
        brokeBlocker: !!trial.brokeBlocker,
        heldScore: opt.result.heldScore,
        baseScore: opt.result.base,
        /* Each run its own colour, so two runs of one improvement read apart. */
        color: SI_TRIAL_COLORS[(trial.n - 1) % SI_TRIAL_COLORS.length],
        letter: `T${trial.n}`,
        finishedAt,
        agentVersion: baseVersion || "trial",
        envVersion: sourceRun?.envVersion,
        scenarioIds: trialIds,
        partial: true,
        total: covered,
        passed,
        flaky: 0,
        dropped: 0,
        measured: covered,
        unmeasured: 0,
        repeats: sourceRun?.repeats || 1,
        meanReturn:
          source?.meanReturn != null
            ? Math.round(
                (source.meanReturn +
                  (lift / 100) * 0.8 +
                  wobble("return", 0.06)) *
                  100,
              ) / 100
            : Math.round((passRate / 100) * 0.9 * 100) / 100,
        passRate,
        /* Read off the run it started from, as that run's own summary shows it. */
        avgDurationMs: Math.round(
          (source?.avgDurationMs || sourceRun?.avgDurationMs || 10000) * jitter,
        ),
        tokens: Math.round(
          (source?.tokens || sourceRun?.tokens || 300000) * jitter,
        ),
        cost:
          Math.round((source?.cost || sourceRun?.cost || 5) * jitter * 100) /
          100,
        saidNotDone: Math.max(
          0,
          Math.round(
            (source?.saidNotDone ?? 2) - lift / 12 + wobble("said", 2),
          ),
        ),
        scores,
        /* Deliberately empty — the compare pipeline hydrates tasks per
           trial in buildComparison (via buildRun + perScenario overlay)
           so we don't rebuild for every summary read. Callers that need
           real per-task data (compare grid, call detail, distribution
           over cells) route through buildComparison; consumers that
           only need aggregates read the summary fields above. */
        tasks: [],
        /* The trial's own per-scenario outcome, light enough for the Runs
           table's counts (runStatsOf reads it when `tasks` is empty). */
        trialTasks: trialIds.map((sid) => {
          const state = trial.perScenario[sid];
          const status =
            state === "fixed" || state === "same"
              ? "passed"
              : state === "flaky"
                ? "flaky"
                : state === "inconclusive"
                  ? "unmeasured"
                  : "failed";
          return {
            id: sid,
            scenarioId: sid,
            status,
            passShare: status === "passed" ? 1 : status === "flaky" ? 2 / 3 : 0,
          };
        }),
        domains: {},
      });
    });
  });

  return results;
};

/**
 * One group descriptor per self improvement.
 *
 * The runs list needs this to draw the parent header row above its
 * trials — trial count, winner, best score, lift.
 */
export const trialGroups = (envState) => {
  const opts = envState?.optimizations || [];
  return opts.map((opt, i) => {
    const trials = opt?.result?.trials || [];
    const winner = opt?.result?.winner;
    return {
      id: opt.id,
      ordinal: i + 1,
      name: improvementRunName(envState, opt),
      color: SI_TRIAL_COLORS[i % SI_TRIAL_COLORS.length],
      status: opt.status,
      trialCount: trials.length,
      winnerTrialN: winner?.n ?? null,
      winnerScore: winner?.score ?? null,
      heldScore: opt?.result?.heldScore ?? null,
      baseScore: opt?.result?.base ?? null,
      lift:
        opt?.result?.heldScore != null && opt?.result?.heldBase != null
          ? opt.result.heldScore - opt.result.heldBase
          : null,
      fromRunId: opt.fromRunId,
      createdAt: opt.createdAt,
    };
  });
};

/* Colours a chart can tell apart, in the order they're handed out. */
const SERIES_PALETTE = [
  "#16A34A",
  "#7857FC",
  "#2563EB",
  "#EA580C",
  "#DB2777",
  "#0891B2",
  "#CA8A04",
  "#DC2626",
  "#0D9488",
  "#65A30D",
  "#C026D3",
  "#525252",
];

/**
 * One colour per eval, never shared. An eval keeps its own colour unless an
 * earlier eval already has it; custom evals all default to the same purple
 * and several catalog evals share a hue, so taking colours as-is drew four
 * evals in two colours. Assigned in eval order, so the same set of evals
 * always gets the same colours.
 */
const distinctColors = (evals) => {
  const taken = new Set();
  const next = () =>
    SERIES_PALETTE.find((c) => !taken.has(c)) ||
    SERIES_PALETTE[taken.size % SERIES_PALETTE.length];
  return evals.map((e) => {
    const own =
      e.color && !taken.has(e.color.toUpperCase())
        ? e.color.toUpperCase()
        : null;
    const c = own || next();
    taken.add(c);
    return c;
  });
};

/** One series per eval across the runs — the trend above the table. */
export const evalSeries = (summaries, envState) => {
  const evals = evalsOf(envState);
  const colors = distinctColors(evals);
  return evals.map((e, i) => ({
    id: e.id,
    name: e.name,
    color: colors[i],
    data: summaries.map((s) =>
      s.scores[e.id] == null ? null : s.scores[e.id],
    ),
  }));
};

/* ── comparison ──────────────────────────────────────────────────────────── */

/**
 * Why a task ended the way it did, in one line.
 *
 * Ranked rather than concatenated. A claim the call log cannot support outranks
 * a failed grader, because the grader read words and the log read the world —
 * and when they disagree the log is the one that found something.
 */
export const decidingMoment = (task) => {
  if (!task)
    return { kind: "missing", text: "This scenario did not run here." };

  /* Attribution first. Everything below reads the agent's behaviour, and none
     of it applies when the agent never got a fair run. */
  const domain = attribute(task);
  if (domain && !domain.measured) {
    return {
      kind: "unmeasured",
      text: `${faultReason(task)} Not counted against the agent.`,
    };
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
    return {
      kind: "tool",
      text: `Never called ${missing}, which this scenario needed.`,
    };
  }
  return {
    kind: "unclear",
    text: "Failed without a grader or a tool call explaining why.",
  };
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
/*
  Unified chip identity across manual runs and trials.

  Each row already carries its own identity (runs: their ordinal number and
  ordinal-keyed colour from runSummary; trials: `T{n}` and their group colour
  from trialSummaries). This just collects them into a lookup so the runs list
  and the compare page render a run with the same label and colour it has in
  the detail header — one identity, assigned once at the source.
*/
/**
 * The number each row shows as "Run N" on the runs list — manual runs and the
 * listed self-improvement trials in one chronological sequence. Compare and
 * the run header read the same numbers, so a run is called the same thing on
 * every screen. The runs list applies exactly this rule.
 */
export const chipIdentity = (env, envState) => {
  /* One identity per row, taken from the row's own stamped letter/colour
     (runs: their ordinal; trials: `T{n}`). Reusing them — rather than
     renumbering by the combined runs+trials timeline — is what keeps the
     list, the detail header and Compare showing the same badge for a run,
     and stops the list chip counting trials that aren't even shown in it. */
  const runs = runSummaries(env, envState);
  const trials = trialSummaries(env, envState);
  const map = new Map();
  [...runs, ...trials].forEach((r) => {
    map.set(r.id, { letter: r.letter, color: r.color });
  });
  return map;
};

/* `foreign` (optional): run summaries from another environment, keyed by the
   id used in `runIds` — a fork's runs compare on carried scenario ids
   (PRD v7 AC-14.14). */
export const buildComparison = (env, envState, runIds, { foreign } = {}) => {
  const runsByRow = runSummaries(env, envState);
  const trialsByRow = trialSummaries(env, envState);
  const chips = chipIdentity(env, envState);
  /* A trial ticked in the runs list is compareable too. Trial summaries
     carry a `tasks: []` stub — hydrate on demand from the trial's source
     run + perScenario overlay so the comparison grid has scenarios to
     render. */
  const runs = runIds
    .map((id) => {
      const run = runsByRow.find((r) => r.id === id) || foreign?.get(id);
      if (run) return run;
      const trialM = /^(OPT-\d+)-t(\d+)$/.exec(id);
      if (!trialM) return null;
      const t = trialsByRow.find((r) => r.id === id);
      if (!t) return null;
      const opt = (envState.optimizations || []).find(
        (o) => o.id === trialM[1],
      );
      const trial = opt?.result?.trials?.find(
        (tr) => tr.n === Number(trialM[2]),
      );
      const per = trial?.perScenario || {};
      const source = (envState.runs || []).find((r) => r.id === opt?.fromRunId);
      if (source) {
        const baseTasks = rebuildRun(env, envState, source);
        const tasks = baseTasks.map((task) => {
          const outcome = per[task.scenarioId] || per[task.id];
          /* Only what the trial changed moves: a fix passes, a break fails,
             and a scenario that stayed the same keeps its own outcome —
             passing, flaky or failing — rather than being flattened. */
          if (!outcome || !TRIAL_CHANGES[outcome]) return task;
          const passed = TRIAL_CHANGES[outcome] === "passed";
          const score = passed ? 0.95 : 0.2;
          const evalResults = (task.evalResults || []).map((r) => ({
            ...r,
            score,
            passed,
          }));
          /* When the trial's outcome differs from the source run's, the
             source's callLog (which reports what the source agent said)
             no longer describes what happened on the trial. Clear the
             "Said, not done" flag on tasks the trial marked passed so
             attribution and the Said-not-done chip don't misreport the
             source run's failure on a trial row. */
          const callLog =
            passed && task.callLog?.unsupportedClaim
              ? { ...task.callLog, unsupportedClaim: null }
              : task.callLog;
          return {
            ...task,
            status: passed ? "passed" : "failed",
            verdict: passed ? "passed" : "failed",
            passShare: passed ? 1 : 0,
            evalResults,
            callLog,
          };
        });
        return { ...t, tasks };
      }
      /*
        Fallback when the source run has been deleted (or was never
        recorded). Run the same buildRun pipeline a real stored run
        would, seeded on the trial id so tasks come back with real
        transcripts, tool calls, evalResults and everything else the
        detail views need. Then overlay the trial's per-scenario
        outcomes so the verdicts match what the trial actually saw.
      */
      const trialIds = new Set(Object.keys(per));
      const scenarios = (envState?.scenarios || []).filter(
        (sc) => !trialIds.size || trialIds.has(sc.id),
      );
      const agent = opt.baseAgentVersion || "v1";
      const built = buildRun({
        seed: id,
        scenarios,
        stage: getSurface(env?.surface).stage,
        evals: evalsOf(envState),
        repeats: 1,
        ...runInputs(env, envState, {
          agent,
          envVersion: runEnvVersion(env, envState, {}),
        }),
      }).tasks.map((task) => ({ ...task, status: task.verdict }));
      const tasks = built.map((task) => {
        const outcome = per[task.scenarioId] || per[task.id];
        if (!outcome || !TRIAL_CHANGES[outcome]) return task;
        const passed = TRIAL_CHANGES[outcome] === "passed";
        const score = passed ? 0.95 : 0.2;
        const evalResults = (task.evalResults || []).map((r) => ({
          ...r,
          score,
          passed,
        }));
        return {
          ...task,
          status: passed ? "passed" : "failed",
          verdict: passed ? "passed" : "failed",
          passShare: passed ? 1 : 0,
          evalResults,
        };
      });
      return { ...t, tasks };
    })
    .filter(Boolean)
    /* Overlay the unified chip identity so the letter/colour a user
       ticked in the runs list is the same one that shows on the
       compare page — the badge is the row's identity, and losing it
       between screens breaks the mental model. */
    .map((r) => {
      const chip = r._env ? null : chips.get(r.id);
      return chip ? { ...r, letter: chip.letter, color: chip.color } : r;
    });

  if (runs.length === 0)
    return { runs: [], rows: [], evals: [], coverage: null };

  const baseline = runs[0];

  /*
    Rows are the union of what the runs covered, not the baseline's set.
    Intersecting would hide scenarios the newer run added; taking the baseline
    alone would silently drop them. Where a run did not cover a row it says so,
    which is the difference between "this got worse" and "this was not tested".
  */
  const seen = new Map();
  runs.forEach((run) =>
    run.tasks.forEach((t) => {
      if (!seen.has(t.id)) seen.set(t.id, t);
    }),
  );
  const scenarios = [...seen.values()];

  const rows = scenarios.map((base) => {
    const baseTask = baseline.tasks.find((t) => t.id === base.id);

    const cells = runs.map((run) => {
      const task = run.tasks.find((t) => t.id === base.id);
      /* An unmeasured cell has no share — not a zero one. Treating it as zero
         is how a dropped session becomes a regression in the next review. */
      const share =
        task && isMeasured(task)
          ? task.passShare ?? (task.status === "passed" ? 1 : 0)
          : null;
      const baseShare =
        baseTask && isMeasured(baseTask)
          ? baseTask.passShare ?? (baseTask.status === "passed" ? 1 : 0)
          : null;
      /*
        One sample flipping is inside the noise of a sampled counterpart. A
        change only counts as movement when it is larger than a single sample
        can account for — otherwise every screen above this one turns run-to-run
        jitter into a regression someone gets paged about.
      */
      const step = 1 / Math.max(1, task?.repeats || run.repeats || 1);
      const delta =
        share == null || baseShare == null ? null : share - baseShare;
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
        /* An unverified cost is no number at all — said as "unverified". */
        cost: task?.costUnverified ? null : task?.cost || 0,
        costUnverified: !!task?.costUnverified,
        /* Deltas are against the baseline, and the baseline has none. Neither
           has a cell whose run never touched this scenario — there is no
           number to subtract, and subtracting anyway printed NaN%. */
        shareDelta: run.id === baseline.id ? null : delta,
        significant:
          run.id === baseline.id
            ? false
            : delta != null && Math.abs(delta) > step + 1e-9,
        durationDelta:
          run.id === baseline.id || !task || !baseTask
            ? null
            : pct(task.durationMs, baseTask.durationMs),
        tokensDelta:
          run.id === baseline.id || !task || !baseTask
            ? null
            : pct(task.tokens, baseTask.tokens),
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
      /* As the Scenarios table names it — old generated titles reworded on read. */
      title: scenarioTitle(base.title),
      task: base.task,
      persona: base.persona,
      critical: base.critical,
      /* Preserved so downstream grouping (by Goal / useCase) has a
         scenario-level label to bucket against. */
      useCase: base.useCase,
      cells,
      /* A row where every run agrees is not where anyone should be looking. */
      changed: verdicts.size > 1,
      /* …and a row that moved by less than one sample is agreement in disguise. */
      significant: moved,
      flaky: cells.some((c) => c.status === "flaky"),
      missing: cells.some((c) => c.status === "missing"),
      unmeasured: cells.some((c) => c.task && !c.measured),
      fixed:
        moved &&
        cells[0]?.measured &&
        cells[0]?.status !== "passed" &&
        cells.slice(1).some((c) => c.measured && c.status === "passed"),
      broke:
        moved &&
        cells[0]?.measured &&
        cells[0]?.status === "passed" &&
        cells.slice(1).some((c) => c.measured && c.status === "failed"),
    };
  });

  /*
    Whether these runs are actually comparable. The screen's whole claim is
    "same scenarios, so the movement is the agent" — it has to stop making that
    claim the moment it stops being true.
  */
  const shared = rows.filter((r) => !r.missing).length;
  /*
    The headline rate is read over the same questions for every run: the
    scenarios every run ran and measured. A whole-run rate from a 4-scenario
    re-run and one from a 68-scenario sweep are different quantities.
  */
  const common = rows.filter((r) =>
    r.cells.every((c) => c.status !== "missing" && c.measured),
  );
  runs.forEach((run, i) => {
    const shares = common
      .map((r) => r.cells[i].passShare)
      .filter((v) => v != null);
    run.sharedPassRate = shares.length
      ? Math.round((shares.reduce((a, b) => a + b, 0) / shares.length) * 100)
      : null;
  });
  /* Whether the runs took the same test: environment version and graders. */
  const envVersions = [
    ...new Set(runs.map((r) => r.envVersion).filter(Boolean)),
  ];
  const graderKey = (r) =>
    JSON.stringify(
      (r.evals || envState?.evals || [])
        .map((e) =>
          typeof e === "string" ? e : `${e.id}:${e.threshold ?? ""}`,
        )
        .sort(),
    );
  const graderSets = new Set(runs.map(graderKey));
  /* The rules each run was graded by. Runs recorded before rules were kept
     have nothing to compare, so they don't count against the rest. */
  const ruleSets = new Set(
    runs
      .filter((r) => Array.isArray(r.rules))
      .map((r) => JSON.stringify([...r.rules].sort())),
  );
  /* Scenarios that are not in every run — most often a batch added between
     them. The headline rate already reads only the shared ones; this says so. */
  const notInAll = rows.filter((r) => r.missing);
  const scenarioById = new Map(
    (envState?.scenarios || []).map((sc) => [sc.id, ensureProvenance(sc)]),
  );
  const firstStart = Math.min(
    ...runs.map(
      (r) => Date.parse(r.startedAt || r.finishedAt || "") || Infinity,
    ),
  );
  const addedAfterFirst = notInAll.filter((r) => {
    const at = Date.parse(scenarioById.get(r.id)?.addedAt || "");
    return Number.isFinite(at) && at > firstStart;
  }).length;
  const coverage = {
    total: rows.length,
    shared,
    common: common.length,
    envVersions,
    sameGraders: graderSets.size <= 1,
    sameRules: ruleSets.size <= 1,
    /* How many scenarios were not in every run, and how many of those were
       added after the earliest run started. */
    notInAll: notInAll.length,
    addedBetween: addedAfterFirst,
    toolGapRuns: runs.filter((r) => r.toolGap?.length).map((r) => r.id),
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
export const changedCount = (comparison) =>
  comparison.rows.filter((r) => r.changed).length;
