/*
  An epoch's per-scenario results, read into the environment's own
  Scenarios table: which set each scenario was in, the one result word,
  and the two calls — before the change and with the winning candidate —
  that the row opens side by side.
*/
import { buildComparison } from "../_mock/comparison";
import { buildRun } from "../_mock/runStream";
import { getSurface } from "../_mock/surfaces";
import { runEnvVersion, runInputs } from "../_mock/toolFit";
import { resolveEval } from "../_mock/evals";
import { scenarioTitle } from "../_mock/scenarios";
import { deriveUseCase } from "../workspace/scenarios/scenarioGoal";

/* One word per result. */
export const RESULT_WORD = {
  broke: "Broke",
  fixed: "Fixed",
  "still-failing": "Still failing",
  same: "Unchanged",
  flaky: "Flaky",
  inconclusive: "Unmeasured",
  "off-script": "Unmeasured",
};

/* Broke first, then Fixed, Still failing, Unchanged, then the rest. */
export const RESULT_ORDER = [
  "broke",
  "fixed",
  "still-failing",
  "same",
  "flaky",
  "inconclusive",
  "off-script",
];
export const rankOf = (outcome) => {
  const i = RESULT_ORDER.indexOf(outcome);
  return i < 0 ? RESULT_ORDER.length : i;
};

/* Why a scenario wasn't measured — the word alone doesn't say. */
export const UNMEASURED_WHY = {
  "off-script": "the caller went off script",
  inconclusive: "the judge couldn't score it",
};

/* The three sets an epoch measures. A scenario is in one only. */
export const EPOCH_SETS = [
  {
    id: "target",
    label: "Target",
    tip: "The failing scenarios the candidates were tuned on",
  },
  {
    id: "held",
    label: "Held back",
    tip: "Never shown to the candidates — they check the change holds",
  },
  {
    id: "mustpass",
    label: "Must-pass",
    tip: "Must-pass scenarios the change must not break — wherever they were measured",
  },
];

/* Target and held back never overlap; must-pass covers its own set and
   any must-pass scenario measured in the other two. */
export const inSet = (entry, setId) =>
  setId === "all" ||
  (setId === "mustpass"
    ? entry.set === "mustpass" || !!entry.critical
    : entry.set === setId);

/* A must-pass scenario: marked, or kept from regression memory. */
const mustPassEntry = (t) => !!t.critical || !!t.remembered;

/**
 * Every scenario the epoch measured, once, with its set and its result:
 * held back first (its outcome is the winner's re-check), then target,
 * then must-pass scenarios not already in either.
 *
 * The verdict's must-pass check is the word on a must-pass scenario: it
 * counts a failure only when it repeats on a re-run (`heldBlockers`). A
 * must-pass scenario the search saw fail once, that passed again on its
 * re-run, didn't break — it reads Unchanged, flagged `recheck` — and one
 * the check says failed again reads Broke, whichever set it was in.
 */
export const epochEntriesOf = (result) => {
  if (!result) return [];
  const winner = result.winner || null;
  const blockers = new Set((result.heldBlockers || []).map((b) => b.id));
  const settle = (t, raw) => {
    if (blockers.has(t.id)) return { outcome: "broke" };
    if (raw === "broke" && mustPassEntry(t))
      return { outcome: "same", recheck: true };
    return { outcome: raw || "same" };
  };
  const out = [
    ...(result.heldTasks || []).map((t) => ({
      ...t,
      set: "held",
      ...settle(t, result.heldPerScenario?.[t.id]),
    })),
    ...(result.trainMeasuredTasks || []).map((t) => ({
      ...t,
      set: "target",
      ...settle(t, winner?.perScenario?.[t.id]),
    })),
  ];
  const taken = new Set(out.map((x) => x.id));
  (result.mustPassTasks || [])
    .filter((t) => !taken.has(t.id))
    .forEach((t) =>
      out.push({
        ...t,
        set: "mustpass",
        ...settle(t, winner?.perScenario?.[t.id]),
      }),
    );
  return out;
};

/** The scenarios the winner broke that aren't must-pass — the verdict names them. */
export const brokeOutsideMustPassOf = (result) =>
  epochEntriesOf(result).filter(
    (e) => e.outcome === "broke" && !mustPassEntry(e),
  );

/** How many scenarios each set holds — counted, never assumed. */
export const setCountsOf = (entries) =>
  Object.fromEntries(
    EPOCH_SETS.map((s) => [s.id, entries.filter((e) => inSet(e, s.id)).length]),
  );

/**
 * The table's rows: the environment's own scenario (so every column reads
 * the same as on the Scenarios tab) with the epoch's entry on `epoch`. A
 * scenario the environment no longer has (a fresh variation drawn for the
 * held-back check) keeps a row of its own.
 */
export const tableRowsOf = (entries, scenarios = [], ownScenarios = []) => {
  const byId = new Map(scenarios.map((s) => [s.id, s]));
  /* The epoch's own scenarios (an issue's) all carry the issue's headline;
     each is named by what it tests instead. */
  const own = new Map(ownScenarios.map((s) => [s.id, s]));
  return entries.map((e) => {
    const sc = byId.get(e.id) || own.get(e.id);
    return sc
      ? {
          ...sc,
          ...(!byId.has(e.id) && { summary: scenarioTitle(e.title) }),
          epoch: e,
        }
      : {
          id: e.id,
          name: scenarioTitle(e.title),
          title: e.title,
          critical: !!e.critical,
          epoch: e,
        };
  });
};

/* The grouping choices this view offers — none, and the Scenarios tab's
   Goal and Persona. */
export const RESULT_GROUPINGS = [
  { id: "none", label: "None", icon: "solar:list-linear" },
  { id: "goal", label: "Goal", icon: "solar:target-linear" },
  { id: "persona", label: "Persona", icon: "solar:user-rounded-linear" },
];

const groupKeyOf = (row, mode) => {
  if (mode === "persona") {
    const name = row?.persona?.name;
    return name
      ? { id: `persona:${name.toLowerCase()}`, label: name }
      : { id: "persona:none", label: "No persona" };
  }
  return deriveUseCase(row);
};

/**
 * Rows sorted by result (Broke first, or the reverse), and grouped. Groups
 * are ordered by the most urgent result they hold, so whatever broke is at
 * the top either way.
 */
export const sortedGroupsOf = (
  rows,
  { groupBy = "goal", dir = "asc" } = {},
) => {
  const sign = dir === "desc" ? -1 : 1;
  const byResult = (a, b) =>
    sign * (rankOf(a.epoch?.outcome) - rankOf(b.epoch?.outcome));
  const sorted = [...rows].sort(byResult);
  if (groupBy === "none") return [{ id: "all", label: null, rows: sorted }];
  const buckets = new Map();
  sorted.forEach((r) => {
    const k = groupKeyOf(r, groupBy);
    if (!buckets.has(k.id))
      buckets.set(k.id, { id: k.id, label: k.label, rows: [] });
    buckets.get(k.id).rows.push(r);
  });
  /* `sorted` already puts the most urgent first, so first-seen order is
     group urgency order. */
  return [...buckets.values()];
};

/* A result as the candidate's call reads: pass share and status. */
const AFTER = {
  fixed: { status: "passed", share: 1 },
  broke: { status: "failed", share: 0 },
  "still-failing": { status: "failed", share: 0 },
  flaky: { status: "flaky", share: 2 / 3 },
  /* A must-pass scenario that passed again on its re-run. */
  recheck: { status: "passed", share: 1 },
};

/* The key a row's candidate call is set by. */
const afterKeyOf = (e) => (e?.recheck ? "recheck" : e?.outcome);

/* A task set to a pass share — status, verdict, passes and graders agree. */
const taskAt = (task, a) => {
  const passed = a.status === "passed";
  return {
    ...task,
    status: a.status,
    verdict: a.status,
    passShare: a.share,
    passes: Math.round(a.share * (task.repeats || 1)),
    evalResults: (task.evalResults || []).map((r) => ({
      ...r,
      score: passed ? 0.95 : 0.2,
      passed,
    })),
    /* A pass calls every tool it needed and claims nothing it didn't do. */
    callLog:
      passed && task.callLog
        ? { ...task.callLog, unsupportedClaim: null, missing: [] }
        : task.callLog,
  };
};

/* The candidate's task for a scenario, as its result says it went. */
const afterTaskOf = (task, outcome) =>
  task && AFTER[outcome] ? taskAt(task, AFTER[outcome]) : task;

/*
  The task before the change, as the result implies it: Fixed and Still
  failing were failing, Broke was passing. The run the epoch started from
  can disagree on a scenario (the epoch re-measured it), and the two
  columns have to tell the same story as the result word.
*/
const beforeTaskOf = (task, outcome) => {
  if (!task) return task;
  const share = task.passShare ?? (task.status === "passed" ? 1 : 0);
  if ((outcome === "fixed" || outcome === "still-failing") && share >= 1)
    return taskAt(task, { status: "failed", share: 0 });
  if (outcome === "broke" && share < 1)
    return taskAt(task, { status: "passed", share: 1 });
  return task;
};

/* The epoch's own measurement flags a claim the call log doesn't back as
   `true`; the call view reads it as a sentence, the way a run states it. */
const callLogOf = (log) => {
  if (!log || typeof log.unsupportedClaim === "string" || !log.unsupportedClaim)
    return log
      ? { ...log, unsupportedClaim: log.unsupportedClaim || null }
      : log;
  const tool = log.missing?.[0];
  return {
    ...log,
    unsupportedClaim: tool
      ? /refund|issue|create|update|cancel|book|escalate/.test(tool)
        ? `The agent told the caller the ${tool.replace(/_/g, " ")} was done, but never called ${tool}.`
        : `The agent told the caller it had checked the ${tool.replace(/_/g, " ")}, but never called ${tool}.`
      : null,
  };
};

/* "2 of 3 trials passed" — or the one call there was. */
export const trialsLine = (task) => {
  if (!task) return null;
  const n = task.repeats || 1;
  const passes =
    task.passes ??
    Math.round((task.passShare ?? (task.status === "passed" ? 1 : 0)) * n);
  if (task.status === "unmeasured" || task.passShare === null)
    return `${n} trial${n === 1 ? "" : "s"}, not scored`;
  return `${passes} of ${n} trial${n === 1 ? "" : "s"} passed`;
};

/**
 * The two calls each row opens: the agent before the change (the run the
 * epoch started from) and the winning candidate. Built with the same
 * comparison the Compare page uses, with the candidate's column set to the
 * epoch's own result for each scenario — the held-back and must-pass
 * re-checks included, which the candidate's search scores don't cover.
 */
export const epochCompareOf = (
  env,
  envState,
  record,
  labels,
  scenarios = envState?.scenarios || [],
) => {
  const result = record?.result;
  const n = record?.preferredTrial || result?.winner?.n;
  if (!env || !envState || !record?.fromRunId || !n) return null;
  const candidateId = `${record.id}-t${n}`;
  const { runs, rows } = buildComparison(env, envState, [
    record.fromRunId,
    candidateId,
  ]);
  if (runs.length < 2) return null;
  const entryOf = Object.fromEntries(
    epochEntriesOf(result).map((e) => [e.id, e]),
  );
  const named = [
    { label: labels.before, letter: "B" },
    { label: labels.candidate(n), letter: `C${n}` },
  ];
  const byId = new Map(
    rows.map((row) => {
      const entry = entryOf[row.id];
      const outcome = entry?.outcome;
      const cells = row.cells.map((c, i) => {
        const task =
          i === 1
            ? afterTaskOf(c.task, afterKeyOf(entry))
            : beforeTaskOf(c.task, outcome);
        return {
          ...c,
          task,
          status: task?.status || c.status,
          passShare: task ? task.passShare ?? c.passShare : c.passShare,
          letter: named[i]?.letter || c.letter,
          label: named[i]?.label,
          note: trialsLine(task),
        };
      });
      return [row.id, { ...row, cells }];
    }),
  );
  /*
    Scenarios the run it started from never had (an issue's own scenarios,
    a fresh variation): the epoch measured them itself before the change,
    so that call is the left column, and the candidate's the right.
  */
  const measured = [...(result.train || []), ...(result.held || [])];
  const colorOf = (i) =>
    rows[0]?.cells?.[i]?.color || runs[i]?.color || "#7857FC";
  const missing = epochEntriesOf(result).filter((e) => !byId.has(e.id));
  /* Their calls, played the way a run plays a scenario — the epoch kept
     only the verdict, not the conversation. */
  const own = (scenarios || []).filter((x) =>
    missing.some((e) => e.id === x.id),
  );
  let played = [];
  try {
    played = own.length
      ? buildRun({
          seed: `${record.id}-own`,
          scenarios: own,
          stage: getSurface(env?.surface).stage,
          evals: (envState?.evals || []).map(resolveEval).filter(Boolean),
          repeats: 1,
          ...runInputs(env, envState, {
            agent: record.baseAgentVersion || "v1",
            envVersion: runEnvVersion(env, envState, {}),
          }),
        }).tasks
      : [];
  } catch {
    played = [];
  }
  missing.forEach((e) => {
    const base = measured.find((t) => t.id === e.id);
    if (!base) return;
    const sc = (scenarios || []).find((x) => x.id === e.id) || null;
    const call = played.find((t) => t.scenarioId === e.id || t.id === e.id);
    /* The epoch's own verdict before the change, on that call. */
    const share = base.passShare ?? (base.status === "passed" ? 1 : 0);
    /* A part pass needs more than one trial to be one. */
    const repeats =
      base.repeats || (share > 0 && share < 1 ? 3 : runs[0]?.repeats || 1);
    const before = beforeTaskOf(
      call
        ? taskAt(
            { ...call, repeats },
            {
              status:
                base.status === "passed"
                  ? "passed"
                  : base.status === "flaky"
                    ? "flaky"
                    : "failed",
              share,
            },
          )
        : {
            ...base,
            scenarioId: base.id,
            repeats,
            callLog: callLogOf(base.callLog),
          },
      e.outcome,
    );
    const after = afterTaskOf(
      { ...before, passShare: before.passShare ?? 0 },
      afterKeyOf(e),
    );
    const cells = [before, after].map((task, i) => ({
      runId: runs[i].id,
      letter: named[i].letter,
      color: colorOf(i),
      task,
      status: task.status,
      measured: true,
      passShare: task.passShare,
      repeats: task.repeats,
      label: named[i].label,
      note: trialsLine(task),
    }));
    byId.set(e.id, {
      id: e.id,
      title: scenarioTitle(e.title),
      task: base.task,
      persona: sc?.persona,
      critical: !!e.critical,
      useCase: sc?.useCase,
      cells,
    });
  });
  return { runs, byId, candidateN: n };
};

/** The trial count a row's result cell shows next to its word. */
export const afterTrialsOf = (compare, id) => {
  const cell = compare?.byId.get(id)?.cells?.[1];
  return cell?.task ? trialsLine(cell.task) : null;
};
