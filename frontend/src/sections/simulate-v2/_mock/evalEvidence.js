/*
  Evaluating saved evidence (evals.md, Run → Evaluate selected evidence):
  which trials, which fields of each trial's evidence an evaluation reads,
  what is missing before anything is spent, and what an evaluation result
  records about itself.

  Shared by the Evaluate tab (Ask a question, Use an evaluation) and by
  "Run evals on these scenarios" on the run's table, so a result looks the
  same wherever it came from. Everything is read from what a run already
  saved — the agent is never run again.

  Saved on the environment with the run's other evaluation results:
  envState.trialEvals[runId] = [result, …], newest first.
*/
import { defaultMappingFor, evalsForEnv, getEval, resolveEval } from "./evals";

/* ── the run's evidence ──────────────────────────────────────────────────── */

export const EVIDENCE = [
  {
    key: "transcript",
    label: "Transcript",
    about: "What the caller and the agent said",
  },
  {
    key: "reply",
    label: "Final reply",
    about: "The agent's last message to the caller",
  },
  {
    key: "tools",
    label: "Tool calls",
    about: "Each tool call and what it returned",
  },
  {
    key: "world",
    label: "World state",
    about: "What the agent changed in the world",
  },
  {
    key: "goal",
    label: "Caller's goal",
    about: "What the scenario asked of the agent",
  },
];
export const evidenceLabelOf = (key) =>
  EVIDENCE.find((e) => e.key === key)?.label || key;

/* Instant eval's "Reads" choice, as the fields it needs. */
export const FIELDS_OF_READS = {
  conversation: ["transcript"],
  conversation_tools: ["transcript", "tools"],
  tools: ["tools"],
};

/*
  Where a mapped column comes from. Anything that isn't the run's own
  evidence — the environment's rules — isn't mapped to a field of a trial.
*/
export function evidenceOfSource(source) {
  const s = String(source || "").toLowerCase();
  if (s.startsWith("env.")) return null;
  if (/world_state|world\./.test(s)) return "world";
  if (s.startsWith("scenario.") || /expected_outcome|^task$|persona/.test(s))
    return "goal";
  if (
    /tool|trajectory|steps|events|patch|test_results|diff|reward|latency/.test(
      s,
    )
  )
    return "tools";
  if (/agent_response|final|reply/.test(s)) return "reply";
  return "transcript";
}

/* ── trials, across runs ─────────────────────────────────────────────────── */

/*
  A trial in an evaluation's scope. Trials of the run on screen keep their
  own id; another run's trial is keyed "runId:trialId", so two runs of the
  same scenario never collide. `sourceTask` is the trial as its run saved it.
*/
export const trialKeyOf = (runId, taskId, thisRunId) =>
  runId === thisRunId ? taskId : `${runId}:${taskId}`;

export const wrapTrials = (runId, runLabel, tasks, thisRunId) =>
  tasks.map((t, i) => ({
    ...t,
    id: trialKeyOf(runId, t.id, thisRunId),
    sourceId: t.id,
    sourceTask: t,
    runId,
    runLabel,
    trialNo: i + 1,
  }));

/* "Trial 14", or "Run 1 · Trial 14" when it isn't from the run on screen. */
export const trialLabelOf = (t, thisRunId) =>
  t.runId && t.runId !== thisRunId
    ? `${t.runLabel} · Trial ${t.trialNo}`
    : `Trial ${t.trialNo}`;

/* Every trial the Evaluate tab can reach, by key. */
export function poolOf(ctx) {
  const all = [
    ...wrapTrials(ctx.runId, ctx.runLabel, ctx.tasks, ctx.runId),
    ...(ctx.others || []).flatMap((r) =>
      wrapTrials(r.id, r.label, r.tasks, ctx.runId),
    ),
  ];
  return new Map(all.map((t) => [t.id, t]));
}

/* ── scope ───────────────────────────────────────────────────────────────── */

export const OUTCOME_SCOPES = [
  { value: "all", label: "All trials" },
  { value: "failed", label: "Failed trials" },
  { value: "passed", label: "Passed trials" },
];

/* The task checks' outcome, in the buckets this screen counts by. */
const outcomeOf = (t) =>
  t.status === "passed"
    ? "passed"
    : t.status === "error"
      ? "error"
      : t.status === "unmeasured"
        ? "unmeasured"
        : "failed";
const inOutcome = (scope, tasks) =>
  scope === "all" ? tasks : tasks.filter((t) => outcomeOf(t) === scope);

/*
  The trials a scope names. All / Failed / Passed apply to every run chosen;
  a filter snapshot or a hand-picked selection belongs to the run it was made
  in, so other runs don't join it.
*/
export function scopeTrialsOf({ scope, ctx, filter, selectedIds, runIds }) {
  const mine = wrapTrials(ctx.runId, ctx.runLabel, ctx.tasks, ctx.runId);
  if (scope === "filter") {
    const ids = new Set(filter?.taskIds || []);
    return mine.filter((t) => ids.has(t.id));
  }
  if (scope === "selected") {
    const ids = new Set(selectedIds || []);
    return mine.filter((t) => ids.has(t.id));
  }
  const extra = (ctx.others || []).filter((r) => (runIds || []).includes(r.id));
  return [
    ...inOutcome(scope, mine),
    ...extra.flatMap((r) =>
      inOutcome(scope, wrapTrials(r.id, r.label, r.tasks, ctx.runId)),
    ),
  ];
}

export const scopeNameOf = ({ scope, filter, selectedIds }) =>
  scope === "filter"
    ? filter?.name || "Current filter"
    : scope === "selected"
      ? `Selected trials (${(selectedIds || []).length})`
      : OUTCOME_SCOPES.find((s) => s.value === scope)?.label || scope;

/* "1–6, 8, 11–12" from trial numbers. */
export function rangesOf(nos) {
  const sorted = [...new Set(nos)].sort((a, b) => a - b);
  const out = [];
  let start = null;
  let prev = null;
  sorted.forEach((n) => {
    if (start === null) {
      start = n;
      prev = n;
    } else if (n === prev + 1) {
      prev = n;
    } else {
      out.push(start === prev ? `${start}` : `${start}–${prev}`);
      start = n;
      prev = n;
    }
  });
  if (start !== null)
    out.push(start === prev ? `${start}` : `${start}–${prev}`);
  return out.join(", ");
}

/* The evidence an evaluation used: per run, which trials. */
export const evidenceRunsOf = (trials, thisRunId) => {
  const by = new Map();
  trials.forEach((t) => {
    const r = by.get(t.runId) || {
      run: t.runId,
      label: t.runLabel,
      here: t.runId === thisRunId,
      nos: [],
      ids: [],
    };
    r.nos.push(t.trialNo);
    r.ids.push(t.sourceId);
    by.set(t.runId, r);
  });
  return [...by.values()];
};

/* ── missing inputs ──────────────────────────────────────────────────────── */

/*
  A field that isn't there is named, per trial, before anything runs. It is
  counted as skipped — never read from some other text in its place. Every
  seventh trial of a run has no saved recording (voice) or messages (chat),
  so the case shows up in the seeded runs.
*/
const NO_TRANSCRIPT_EVERY = 7;

export function missingFieldsOf(trial, fields, ctx) {
  const out = [];
  const add = (key, why) =>
    out.push({
      key,
      label: evidenceLabelOf(key),
      text: `${why} for ${evidenceLabelOf(key)}`,
    });
  const unfinished = trial.status === "error";
  const noSpeech = trial.trialNo % NO_TRANSCRIPT_EVERY === 0;
  fields.forEach((key) => {
    if (key === "goal") return;
    if (unfinished) return add(key, "nothing saved, the trial didn't finish");
    if (key === "transcript" || key === "reply") {
      if (noSpeech) add(key, ctx.voice ? "no recording" : "no saved messages");
      return;
    }
    if (key === "tools") {
      if (!(trial.callLog?.length || trial.steps?.length))
        add(key, "no calls saved");
      return;
    }
    if (key === "world" && !ctx.worldHeld) add(key, "no snapshot");
  });
  return out;
}

/* Which trials can't be read, and what each lacks. */
export function missingOf(trials, fields, ctx) {
  return trials
    .map((t) => {
      const lacks = missingFieldsOf(t, fields, ctx);
      return lacks.length
        ? {
            key: t.id,
            trial: t,
            lacks,
            line: `${trialLabelOf(t, ctx.runId)} · ${lacks.map((l) => l.text).join(", ")}`,
          }
        : null;
    })
    .filter(Boolean);
}

/* { trialKey: "no recording for Transcript" } — what a result keeps. */
export const skippedMapOf = (missing) =>
  Object.fromEntries(
    missing.map((m) => [m.key, m.lacks.map((l) => l.text).join(", ")]),
  );

/* ── grading errors ──────────────────────────────────────────────────────── */

export const GRADING_ERROR = "Grading error";
export const GRADING_ERROR_REASON = "Grading error · judge didn't answer";

const hash = (s) =>
  [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
export const hashOf = hash;

/*
  The judge sometimes doesn't answer. A small, steady share of the trials
  that were read come back with a grading error — at least one when four or
  more were read. It is counted on its own, never as a score of zero and
  never as a pass or a fail.
*/
export function gradingErrorKeys(keys, salt) {
  const ranked = [...keys].sort(
    (a, b) => hash(`${salt}|${a}`) - hash(`${salt}|${b}`),
  );
  if (keys.length >= 4)
    return new Set(
      ranked.slice(0, Math.max(1, Math.round(keys.length * 0.06))),
    );
  return new Set(ranked.filter((k) => hash(`${salt}|${k}|e`) % 7 === 0));
}

/* ── evaluations ─────────────────────────────────────────────────────────── */

const libraryVersion = (id) => 1 + (hash(id) % 3);
const editsOf = (name, envState) =>
  (envState?.evalChanges || []).filter(
    (c) => typeof c.detail === "string" && c.detail === `Edited ${name}`,
  ).length;

/* "v3": the library's current version, moved by each edit made here. */
export function evaluationVersionOf(ev, envState) {
  if (ev?.version)
    return String(ev.version).startsWith("v") ? ev.version : `v${ev.version}`;
  return `v${libraryVersion(ev?.id || "") + editsOf(ev?.instanceName || ev?.name, envState)}`;
}

const prettyField = (s) =>
  s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/* The fields an evaluation needs, each with the evidence it defaults to. */
export function requiredFieldsOf(ev) {
  const mapping =
    ev?.mapping && Object.keys(ev.mapping).length
      ? ev.mapping
      : defaultMappingFor(ev?.id);
  const entries = Object.entries(mapping);
  const list = entries.length ? entries : [["output", "transcript"]];
  return list.map(([name, source]) => {
    const evidence = evidenceOfSource(source);
    return {
      name,
      label: prettyField(name),
      source: String(source),
      evidence,
      fromEnvironment: evidence === null,
    };
  });
}

/* The evaluations to pick from: what the environment applies, then the platform's. */
export function evaluationsOf(env, envState) {
  const present = (ev) => {
    const r = resolveEval(ev);
    if (!r) return null;
    return {
      id: r.id,
      name: r.instanceName || r.name || r.id,
      version: evaluationVersionOf(r, envState),
      about: r.blurb || "",
      type: r.type,
      threshold: r.threshold ?? 0.8,
      icon: r.icon,
      mapping: r.mapping,
    };
  };
  const applied = (envState?.evals || []).map(present).filter(Boolean);
  const appliedIds = new Set(applied.map((e) => e.id));
  const platform = evalsForEnv(env, envState)
    .filter((e) => !appliedIds.has(e.id))
    .map(present)
    .filter(Boolean);
  return { applied, platform };
}
export const evaluatorLabelOf = (e) => `${e.name} ${e.version}`;

/* ── grading one trial ───────────────────────────────────────────────────── */

/* A trial's score for one evaluation — steady for the same result and trial. */
export function gradeTrial({ key, evalId, threshold = 0.8, salt, task }) {
  const h = hash(`${evalId}|${key}|${salt}`);
  const passed = task?.status === "passed";
  const base = passed ? 0.62 : 0.42;
  const score = Math.round((base + (h % 38) / 100) * 100) / 100;
  const ok = score >= threshold;
  return {
    score,
    passed: ok,
    threshold,
    reason: ok
      ? "Met the configured threshold."
      : `Scored ${Math.round(score * 100)} against a threshold of ${Math.round(threshold * 100)}.`,
  };
}

/* ── result identity ─────────────────────────────────────────────────────── */

let seq = 0;
/* EVR-4K2Q: one per saved evaluation result. */
export function newResultId() {
  seq += 1;
  return `EVR-${(Date.now() + seq).toString(36).toUpperCase().slice(-4)}`;
}

/* What a saved result says about the evidence it read. */
export function evidenceOf({ fields, trials, thisRunId }) {
  return {
    fields,
    runs: evidenceRunsOf(trials, thisRunId).map((r) => ({
      run: r.run,
      label: r.label,
      trials: r.nos,
      ids: r.ids,
    })),
  };
}

/*
  Runs one evaluation over a set of trials: a score for each trial that could
  be read, the ones that couldn't, and the judge's own failures — kept apart.
*/
export function runEvaluation({
  evaluation,
  mapping,
  trials,
  ctx,
  scope,
  scopeLabel,
  source = "evaluate",
}) {
  const id = newResultId();
  const fields = [
    ...new Set(Object.values(mapping || {}).filter((v) => v && v !== "env")),
  ];
  const missing = missingOf(trials, fields, ctx);
  const lacking = new Set(missing.map((m) => m.key));
  const readable = trials.filter((t) => !lacking.has(t.id));
  const errors = gradingErrorKeys(
    readable.map((t) => t.id),
    id,
  );
  const results = {};
  readable.forEach((t) => {
    results[t.id] = errors.has(t.id)
      ? { error: true, reason: GRADING_ERROR_REASON }
      : gradeTrial({
          key: t.id,
          evalId: evaluation.id,
          threshold: evaluation.threshold,
          salt: id,
          task: t,
        });
  });
  return {
    id,
    kind: "evaluation",
    source,
    at: new Date().toISOString(),
    by: "You",
    evaluator: {
      id: evaluation.id,
      name: evaluation.name,
      version: evaluation.version,
      icon: evaluation.icon,
      threshold: evaluation.threshold,
    },
    mapping,
    scope,
    scopeLabel,
    taskIds: trials.map((t) => t.id),
    evidence: evidenceOf({ fields, trials, thisRunId: ctx.runId }),
    results,
    skipped: skippedMapOf(missing),
    credits: readable.length * 0.0035,
    durationS: Math.max(6, Math.round(readable.length * 0.6)),
  };
}

/* Counts for an evaluation result: what was judged, apart from what wasn't. */
export function evaluationCountsOf(result) {
  const rows = Object.values(result.results || {});
  const graded = rows.filter((r) => !r.error);
  return {
    requested: (result.taskIds || []).length,
    evaluated: graded.length,
    passed: graded.filter((r) => r.passed).length,
    failed: graded.filter((r) => !r.passed).length,
    errors: rows.length - graded.length,
    skipped: Object.keys(result.skipped || {}).length,
  };
}

/* The one line every answers view and column carries. */
export const denominatorLine = ({ evaluated, requested, skipped, errors }) =>
  [
    `${evaluated} evaluated of ${requested} requested`,
    skipped ? `${skipped} not read` : null,
    errors ? `${errors} grading ${errors === 1 ? "error" : "errors"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

/* A saved result's title in lists. */
export const resultTitleOf = (r) =>
  r.kind === "evaluation"
    ? evaluatorLabelOf(r.evaluator)
    : r.questions?.[0]?.text || r.id;

/* The evaluations a run's table shows as extra columns (Run evals on these scenarios). */
export const columnResultsOf = (saved = []) =>
  saved.filter((r) => r.kind === "evaluation" && r.source === "scenarios");

export const getEvalIcon = (id) => getEval(id)?.icon;
