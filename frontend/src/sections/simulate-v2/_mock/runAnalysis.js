import { runInsightsOf } from "./runInsights";
import { scenarioTitle } from "./scenarios";

/*
  A simulation run as an Insights report (the analysis screen Observe's
  Insights draws): the run's failure issues are its categories, the scenarios
  it measured are the units, and every earlier run of the environment is a
  point on the "across runs" chart. Category ids are the issue's root key, so
  one cause is one series across runs; the run on screen takes the first colours.
*/

const OTHER_KEY = "other";
const SEVERITY = {
  critical: "high",
  high: "high",
  medium: "medium",
  low: "low",
};
const MAX_FINDINGS = 5;
const MAX_HIGHLIGHTS = 4;
const pct = (n) => `${Math.round(n * 100)}%`;
const titleOf = (t) => scenarioTitle(t?.title) || t?.name || t?.id;

/* The categories one run's failures fall into, largest first. */
const categoriesOf = (ins) => {
  const analysed = ins.failed + ins.mixed;
  const cats = ins.issues.map((i) => ({
    id: i.key,
    name: i.title,
    level: 1,
    parent_id: null,
    status: "active",
    description: i.root?.label,
    has_children: false,
    children: [],
    notable: [],
    stats: {
      count: i.members.length,
      share: analysed ? i.members.length / analysed : 0,
      failure_rate: i.members.length ? i.failed / i.members.length : 0,
    },
  }));
  const loose = analysed - cats.reduce((a, c) => a + c.stats.count, 0);
  if (loose > 0)
    cats.push({
      id: OTHER_KEY,
      name: "Other",
      level: 1,
      parent_id: null,
      status: "active",
      description:
        "Failures that don't share a cause with another scenario yet",
      has_children: false,
      children: [],
      notable: [],
      stats: { count: loose, share: analysed ? loose / analysed : 0 },
    });
  return cats.sort((a, b) => b.stats.count - a.stats.count);
};

const distributionOf = (cats) =>
  cats.map((c) => ({
    category_id: c.id,
    name: c.name,
    count: c.stats.count,
    share: c.stats.share,
    status: c.status,
  }));

/* What moved since the run before — the change cards. */
const changesOf = (cats, prev, ins, prevIns) => {
  if (!prev) return null;
  const before = new Map(prev.map((c) => [c.id, c]));
  const rate = (x) => (x.measured ? (x.failed + x.mixed) / x.measured : 0);
  return {
    categories: cats.map((c) => {
      const p = before.get(c.id);
      const delta = c.stats.share - (p?.stats.share || 0);
      return {
        category_id: c.id,
        name: c.name,
        share: c.stats.share,
        previous_share: p?.stats.share || 0,
        delta,
        count: c.stats.count,
        previous_count: p?.stats.count || 0,
        significant: Math.abs(delta) >= 0.1 && c.stats.count >= 2,
      };
    }),
    new_category_ids: cats
      .filter((c) => !before.has(c.id) && c.id !== OTHER_KEY)
      .map((c) => c.id),
    metrics: [
      {
        metric: "failure_rate",
        label: "Failing scenarios",
        value: rate(ins),
        previous: rate(prevIns),
        significant: Math.abs(rate(ins) - rate(prevIns)) >= 0.1,
      },
    ],
  };
};

/*
  The report for one run. `history` is the environment's run summaries
  (oldest first, each carrying its rebuilt tasks); `runId` the one on screen.
*/
export function runAnalysisOf({
  env,
  envState,
  tasks,
  history = [],
  runId,
  goalOf,
}) {
  const done = history.filter((r) => r.finishedAt && r.tasks?.length);
  const pastOf = new Map(
    done.map((r) => {
      const ins = runInsightsOf({
        tasks: r.tasks,
        env,
        envState,
        run: r,
        goalOf,
      });
      return [r.id, { ins, cats: categoriesOf(ins) }];
    }),
  );
  const at = done.findIndex((r) => r.id === runId);
  const ins = runInsightsOf({
    tasks,
    env,
    envState,
    run: done[at] || null,
    goalOf,
  });
  const cats = categoriesOf(ins);
  const prevRun = at > 0 ? done[at - 1] : null;
  const prev = prevRun && pastOf.get(prevRun.id);

  /* "#n" citations: each scenario a finding names gets the next number. */
  const refs = new Map();
  const refOf = (t) => {
    if (!refs.has(t.id)) refs.set(t.id, refs.size + 1);
    return refs.get(t.id);
  };
  const previews = new Map();
  const findings = ins.issues.slice(0, MAX_FINDINGS).map((i) => {
    const cited = i.members.slice(0, 3);
    cited.forEach((t) =>
      previews.set(t.id, {
        title: titleOf(t),
        summary: i.root?.label,
        reason: i.root?.label,
        categoryId: i.key,
      }),
    );
    const n = i.members.length;
    return {
      headline: `${i.title}: ${n} of ${ins.measured} scenarios (${cited.map((t) => `#${refOf(t)}`).join(", ")}).`,
      detail: [
        i.root?.label,
        i.symptoms?.[0] && `Also seen: ${i.symptoms[0].label.toLowerCase()}`,
      ]
        .filter(Boolean)
        .join(". "),
      action: i.fix?.text || "",
      severity: SEVERITY[i.severity] || null,
      category_ids: [i.key],
      refs: cited.map((t) => ({ ref: refOf(t), unit_id: t.id })),
    };
  });
  const highlights = ins.issues.slice(0, MAX_HIGHLIGHTS).map((i) => {
    const lead = i.members[0];
    return {
      ref: refOf(lead),
      unit_id: lead.id,
      category_id: i.key,
      title: titleOf(lead),
      summary: i.root?.label,
      reason: i.root?.label,
    };
  });

  const analysed = ins.failed + ins.mixed;
  const tasksById = new Map(tasks.map((t) => [t.id, t]));
  const runs = done.map((r) => ({
    id: r.id,
    status: "completed",
    created_at: r.startedAt || r.finishedAt,
    finished_at: r.finishedAt,
    analysed_count: pastOf
      .get(r.id)
      .cats.reduce((a, c) => a + c.stats.count, 0),
    distribution: distributionOf(pastOf.get(r.id).cats),
    label: `Run ${r.ordinal}`,
  }));
  const here = done[at];
  const run = {
    id: runId,
    status: "completed",
    unit: "scenarios",
    created_at: here?.startedAt,
    started_at: here?.startedAt,
    finished_at: here?.finishedAt,
    sample_size: tasks.length,
    population_size: tasks.length,
    analysed_count: analysed,
    taxonomy_mode: "discover",
    settings: { lens: "failures", sampling: "all" },
    result: {
      categories: cats,
      excluded: ins.passed,
      totals: { count: analysed },
      engine: { kind: "builtin", version: "simulate/v1" },
      changes: prev ? changesOf(cats, prev.cats, ins, prev.ins) : null,
      summary: {
        findings,
        highlights,
        verification: { quotes: false },
      },
    },
  };
  return {
    /* This run's causes take the first colours; older runs' fill the rest. */
    config: {
      id: env?.id,
      lens: "failures",
      taxonomy: { tops: cats.filter((c) => c.id !== OTHER_KEY) },
      runs: [...runs].reverse(),
    },
    run,
    previews,
    ordinal: here?.ordinal,
    counts: {
      total: tasks.length,
      measured: ins.measured,
      passed: ins.passed,
      failed: ins.failed,
      mixed: ins.mixed,
      notMeasured: ins.notMeasured.count,
    },
    membersOf: (key) =>
      key === OTHER_KEY
        ? ins.unclustered.ids
        : ins.issues.find((i) => i.key === key)?.memberIds || [],
    taskOf: (id) => tasksById.get(id),
    pct,
  };
}
