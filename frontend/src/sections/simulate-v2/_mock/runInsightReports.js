/*
  Insights on a run (evals.md, Run → Insights / Debug failures) — the same
  reports as Observe's Insights, over one run's trials instead of traces.
  Nothing runs until the user asks: a report is started from a template or
  a question, reads the trials it names, and is saved with the run.

  A failures report is built from the run's own failure groups
  (runInsightsOf), so its categories match the Scenarios tab. It cites the
  trials behind every finding and keeps what was observed apart from what's
  only a likely reason.

  Saved on the environment: envState.insightReports[runId] = [report, …].
*/
import { scenarioTitle } from "./scenarios";
import { temperamentOf } from "./runInsights";

export const TEMPLATES = [
  {
    id: "failures",
    title: "Where is the agent failing?",
    about: "Groups the failed trials by what went wrong, with a fix for each",
    icon: "solar:danger-triangle-linear",
    scope: "failed",
  },
  {
    id: "goals",
    title: "What are callers asking for?",
    about:
      "Groups every trial by the caller's goal, with how often each passes",
    icon: "solar:chat-round-line-linear",
    scope: "all",
  },
  {
    id: "endings",
    title: "How do conversations end?",
    about: "Groups every trial by how the conversation ended",
    icon: "solar:flag-linear",
    scope: "all",
  },
  {
    id: "custom",
    title: "Ask your own question",
    about: "Your question, answered over the trials you pick",
    icon: "solar:question-circle-linear",
    scope: "failed",
  },
];
export const templateOf = (id) =>
  TEMPLATES.find((t) => t.id === id) || TEMPLATES[0];

export const SCOPE_LABEL = { failed: "Failed trials", all: "All trials" };

const isPass = (t) => t.status === "passed";
const isMeasured = (t) => t.status !== "unmeasured" && t.status !== "error";
export const scopeIdsOf = (scope, tasks) =>
  (scope === "failed"
    ? tasks.filter((t) => isMeasured(t) && !isPass(t))
    : tasks
  ).map((t) => t.id);

/* Reading a trial for a report: a little more than one Instant eval answer. */
const CREDITS_PER_TRIAL = 0.012;
export const reportCreditsOf = (n) => n * CREDITS_PER_TRIAL;

const hash = (s) =>
  [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const pct = (n, d) => (d ? Math.round((n / d) * 100) : 0);

/* The trial number a citation shows: its place in the run. */
export const trialNoOf = (tasks, id) => tasks.findIndex((t) => t.id === id) + 1;

const SEVERITY_ROLE = {
  critical: "danger",
  high: "danger",
  medium: "warning",
  low: "neutral",
};

/* ── failures: the run's own failure groups ─────────────────────────────── */
function failuresReport(insights, inScope) {
  const ids = new Set(inScope);
  const issues = (insights.issues || [])
    .map((i) => ({ ...i, ids: i.memberIds.filter((x) => ids.has(x)) }))
    .filter((i) => i.ids.length);
  const n = inScope.length;
  /* The table reads biggest first; findings keep severity order. */
  const categories = [...issues]
    .sort((a, b) => b.ids.length - a.ids.length)
    .map((i) => ({
      key: i.id,
      label: i.title,
      about: i.root.label,
      ids: i.ids,
      stands: [
        i.critFails
          ? `${i.critFails} critical sub-goal${i.critFails === 1 ? "" : "s"} missed`
          : null,
        i.personas
          ? `${i.personas.temperament} callers ${i.personas.count} of ${i.members.length}`
          : null,
        i.blockers
          ? `${i.blockers} must-pass scenario${i.blockers === 1 ? "" : "s"}`
          : null,
      ].filter(Boolean),
      subs: (i.symptoms || [])
        .map((s) => ({ label: s.label, ids: s.ids.filter((x) => ids.has(x)) }))
        .filter((s) => s.ids.length),
      issueId: i.id,
    }));
  const findings = issues.slice(0, 5).map((i) => {
    const also = i.symptoms?.[0]?.label;
    return {
      key: i.id,
      categoryKey: i.id,
      role: SEVERITY_ROLE[i.severity] || "neutral",
      title: `${i.title} in ${i.ids.length} ${i.ids.length === 1 ? "trial" : "trials"} (${pct(i.ids.length, n)}%)`,
      observed: `${i.root.label}.${also ? ` Also seen: ${also.charAt(0).toLowerCase()}${also.slice(1)}.` : ""}`,
      cites: i.ids.slice(0, 3),
      fix: i.fix?.text || null,
      issueId: i.id,
    };
  });
  const clustered = new Set(issues.flatMap((i) => i.ids));
  return {
    heading: "Where does the agent fall short?",
    categories,
    findings,
    leftOver: inScope.filter((x) => !clustered.has(x)).length,
  };
}

/* ── goals: what callers came for, and how often it worked ─────────────── */
function goalsReport(insights, inScope) {
  const ids = new Set(inScope);
  const goals = (insights.goals || [])
    .map((g) => ({ ...g, ids: g.ids.filter((x) => ids.has(x)) }))
    .filter((g) => g.ids.length)
    .sort((a, b) => b.ids.length - a.ids.length);
  const categories = goals.map((g, i) => ({
    key: `goal-${i}`,
    kind: "goal",
    label: g.goal,
    about: `${g.ids.length} trials`,
    ids: g.ids,
    stands: [
      g.measured ? `${pct(g.passed, g.measured)}% passed` : "Not measured",
      g.critical ? "Has must-pass scenarios" : null,
    ].filter(Boolean),
    subs: [],
  }));
  const findings = goals
    .filter((g) => g.measured >= 3 && g.failingIds.length)
    .sort((a, b) => a.passed / a.measured - b.passed / b.measured)
    .slice(0, 4)
    .map((g, i) => ({
      key: `goal-f-${i}`,
      categoryKey: categories.find((c) => c.label === g.goal)?.key,
      role: pct(g.passed, g.measured) < 50 ? "danger" : "warning",
      title: `${g.goal} passes ${pct(g.passed, g.measured)}% of the time (${g.passed} of ${g.measured})`,
      observed: `${g.measured - g.passed} of ${g.measured} measured trials with this goal didn't pass.`,
      cites: g.failingIds.filter((x) => ids.has(x)).slice(0, 3),
      fix: null,
    }));
  return {
    heading: "Where do callers come away without what they asked for?",
    categories,
    findings,
    leftOver: 0,
  };
}

/* ── endings: how each conversation finished ───────────────────────────── */
const ENDINGS = [
  {
    key: "resolved",
    label: "Resolved",
    about: "The agent finished the task and the caller agreed",
  },
  {
    key: "handoff",
    label: "Handed to a person",
    about: "The agent passed the caller to a person",
  },
  {
    key: "gave-up",
    label: "Caller gave up",
    about: "The caller stopped before it was settled",
  },
  {
    key: "other",
    label: "Other",
    about: "No clear ending in the conversation",
  },
];
const endingOf = (t) => {
  const r = hash(`ending|${t.id}`) % 100;
  if (isPass(t)) return r < 78 ? 0 : r < 90 ? 1 : 3;
  return r < 20 ? 0 : r < 45 ? 1 : r < 80 ? 2 : 3;
};
function endingsReport(inScope, tasks) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const groups = ENDINGS.map((e) => ({ ...e, ids: [] }));
  inScope.forEach((id) => groups[endingOf(byId.get(id))].ids.push(id));
  const categories = groups
    .filter((g) => g.ids.length)
    .map((g) => {
      const failed = g.ids.filter((x) => !isPass(byId.get(x))).length;
      return {
        key: g.key,
        label: g.label,
        about: g.about,
        ids: g.ids,
        stands: [`${pct(failed, g.ids.length)}% failed their checks`],
        subs: [],
      };
    });
  const gaveUp = groups[2];
  const resolvedButFailed = groups[0].ids.filter((x) => !isPass(byId.get(x)));
  const findings = [
    gaveUp.ids.length && {
      key: "gave-up",
      categoryKey: "gave-up",
      role: "danger",
      title: `Callers gave up in ${gaveUp.ids.length} trials (${pct(gaveUp.ids.length, inScope.length)}%)`,
      observed: "The caller stopped replying before the task was settled.",
      cites: gaveUp.ids.slice(0, 3),
      fix: null,
    },
    resolvedButFailed.length && {
      key: "resolved-failed",
      categoryKey: "resolved",
      role: "warning",
      title: `${resolvedButFailed.length} conversations ended resolved but failed their checks`,
      observed:
        "The caller left satisfied, but the world state shows the task wasn't done — nobody would notice from the conversation alone.",
      cites: resolvedButFailed.slice(0, 3),
      fix: null,
    },
  ].filter(Boolean);
  return {
    heading: "How do conversations end?",
    categories,
    findings,
    leftOver: 0,
  };
}

/* Picks the trials a highlighted example shows: one per category. */
function highlightsOf(categories, tasks) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  return categories.slice(0, 6).map((c) => {
    const t = byId.get(c.ids[0]);
    return {
      key: c.key,
      taskId: t.id,
      title: scenarioTitle(t.title || t.name),
      body:
        c.kind === "goal"
          ? `${t.persona?.name || "The caller"} came for this, and the trial ${isPass(t) ? "passed" : "didn't pass"} its checks.`
          : `${t.persona?.name || "The caller"}: ${c.about.charAt(0).toLowerCase()}${c.about.slice(1)}.`,
      categoryKey: c.key,
    };
  });
}

/*
  A report shows at most seven categories, each in its own colour; the rest
  fold into "Other" (the neutral colour), listed inside it.
*/
export const REST_KEY = "rest";
const MAX_CATEGORIES = 7;
function capped(categories) {
  if (categories.length <= MAX_CATEGORIES) return categories;
  const sorted = [...categories].sort((a, b) => b.ids.length - a.ids.length);
  const keep = sorted.slice(0, MAX_CATEGORIES - 1);
  const rest = sorted.slice(MAX_CATEGORIES - 1);
  return [
    ...keep,
    {
      key: REST_KEY,
      label: "Other",
      about: `${rest.length} smaller categories`,
      ids: [...new Set(rest.flatMap((c) => c.ids))],
      stands: [],
      subs: rest.map((c) => ({ label: c.label, ids: c.ids })),
    },
  ];
}

/** Builds one report over the run's trials. */
export function buildRunReport({
  template,
  question,
  scope,
  insights,
  tasks,
  runId,
}) {
  const inScope = scopeIdsOf(scope, tasks);
  const body =
    template === "goals"
      ? goalsReport(insights, inScope)
      : template === "endings"
        ? endingsReport(inScope, tasks)
        : failuresReport(insights, inScope);
  return {
    id: `RPT-${Date.now().toString(36).toUpperCase()}`,
    at: new Date().toISOString(),
    by: "You",
    runId,
    template,
    title:
      template === "custom" && question?.trim()
        ? question.trim()
        : templateOf(template).title,
    scope,
    total: tasks.length,
    analysed: inScope.length,
    credits: reportCreditsOf(inScope.length),
    ...body,
    categories: capped(body.categories),
    highlights: highlightsOf(capped(body.categories), tasks).filter(
      (h) => h.categoryKey !== REST_KEY,
    ),
    asks: [],
  };
}

/* Three trials read with the report's settings, before anything is spent. */
export function previewOf({ template, scope, insights, tasks }) {
  const r = buildRunReport({
    template,
    scope,
    insights,
    tasks,
    runId: "preview",
  });
  return r.categories
    .flatMap((c) => c.ids.slice(0, 1).map((id) => ({ id, label: c.label })))
    .slice(0, 3);
}

/* ── Ask about this report ─────────────────────────────────────────────── */
export const ASK_SUGGESTIONS = [
  "Which personas are hit hardest?",
  "Which scenarios fail every repeat?",
  "What do the passing trials do differently?",
];

/*
  An answer names its scope, cites trials, and keeps what was observed
  apart from a likely reason. Too little evidence says so.
*/
export function answerAsk(report, question, tasks) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const ids = report.categories.flatMap((c) => c.ids);
  const trials = [...new Set(ids)].map((id) => byId.get(id)).filter(Boolean);
  const scope = `${trials.length} trials in this report`;
  const q = question.toLowerCase();
  if (/persona|caller|who/.test(q)) {
    const by = new Map();
    trials.forEach((t) => {
      const k = t.persona?.name || "Unnamed caller";
      by.set(k, [...(by.get(k) || []), t]);
    });
    const top = [...by.entries()]
      .sort((a, b) => b[1].length - a[1].length)
      .slice(0, 3);
    const temps = trials.map((t) => temperamentOf(t.persona)).filter(Boolean);
    const hostile = temps.filter((x) => x === "hostile").length;
    return {
      question,
      scope,
      observed: top.map(
        ([name, ts]) =>
          `${name} appears in ${ts.length} of ${trials.length} trials.`,
      ),
      hypothesis: hostile
        ? `${hostile} of these trials are pushy or hostile callers — the agent may give way under pressure.`
        : "No one temperament stands out, so the cause is more likely the scenario than the caller.",
      cites: top.flatMap(([, ts]) => ts.slice(0, 1).map((t) => t.id)),
    };
  }
  if (/repeat|every|always|scenario/.test(q)) {
    const by = new Map();
    tasks.forEach((t) => {
      const k = scenarioTitle(t.title || t.name);
      by.set(k, [...(by.get(k) || []), t]);
    });
    const always = [...by.entries()].filter(
      ([, ts]) => ts.length > 1 && ts.every((t) => isMeasured(t) && !isPass(t)),
    );
    if (!always.length)
      return {
        question,
        scope: "every trial in this run",
        observed: ["No scenario failed on every repeat."],
        hypothesis:
          "Failures here come and go between repeats, so look at what differs between a passing and a failing repeat.",
        cites: [],
      };
    return {
      question,
      scope: "every trial in this run",
      observed: always
        .slice(0, 4)
        .map(([name, ts]) => `${name} failed all ${ts.length} repeats.`),
      hypothesis:
        "A scenario that fails every time is a steady gap in the agent, not bad luck — the best place to start a fix.",
      cites: always.slice(0, 4).map(([, ts]) => ts[0].id),
    };
  }
  if (/pass|differ|recover|work/.test(q)) {
    const passing = tasks.filter(isPass);
    const lead = report.findings[0];
    return {
      question,
      scope: `${passing.length} passing and ${trials.length} failing trials`,
      observed: [
        `${passing.length} trials passed every check.`,
        lead
          ? `None of the passing trials show “${lead.title.replace(/ in \d+ trials.*$/, "").toLowerCase()}”.`
          : null,
      ].filter(Boolean),
      hypothesis: lead?.fix
        ? `The passing trials already do what the fix asks: ${lead.fix.charAt(0).toLowerCase()}${lead.fix.slice(1)}`
        : "The passing trials finish the task before the caller pushes back.",
      cites: passing.slice(0, 2).map((t) => t.id),
    };
  }
  return {
    question,
    scope,
    observed: [],
    hypothesis: null,
    tooLittle:
      "There isn't enough in this report's trials to answer that. Try a narrower question about personas, scenarios or what passing trials do, or run more trials.",
    cites: [],
  };
}

export const whenOf = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
