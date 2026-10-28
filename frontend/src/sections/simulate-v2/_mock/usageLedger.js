/*
  The workspace's simulation usage this period, as ledger entries — one per
  finished run, priced with the same per-call rules as the run's Cost tab, so
  the two never disagree, and one per finished improvement for what its
  search spent. Grouped by environment, agent, project, user or action for
  Settings → Usage.
*/
import { rebuildRun } from "./comparison";
import { envOf } from "./runGroup";
import { resolveEval } from "./evals";
import { judgeCreditsOf, judgesOf, projectOf, usageOfTask } from "./pricing";
import { FREE_TIER, RATE_PLAN } from "./rates";
import { creditsForUsd } from "./metering";
import { improvementSpendParts, OPT_STATUS } from "./optimizationRuns";

export { projectOf };

/* Who started a run, as the run recorded it (a person, a schedule or a fix). */
const userOf = (run) => run?.startedBy || "Not recorded";

export const GROUPS = [
  { id: "environment", label: "Environment" },
  { id: "agent", label: "Agent" },
  { id: "project", label: "Project" },
  { id: "user", label: "User" },
  { id: "action", label: "Action" },
];

/**
 * One finished run's charge, with the per-call rules of its Cost tab:
 * { sim, evals, calls, credits } — or null when it can't be rebuilt.
 */
export const priceRun = (env, es, run) => {
  let tasks = [];
  try {
    tasks = rebuildRun(env, es, run);
  } catch {
    return null;
  }
  const voice = ["voice", "multi"].includes(env?.surface);
  const evals = (run.evals || es.evals || []).map(resolveEval).filter(Boolean);
  const opts = {
    voice,
    judgeCredits: judgeCreditsOf(evals),
    judges: judgesOf(evals),
    repeats: run.repeats || 1,
  };
  let sim = 0;
  let ev = 0;
  let calls = 0;
  let qty = 0;
  tasks.forEach((t) => {
    const u = usageOfTask(t, opts);
    sim += u.simCredits;
    ev += u.evalCredits;
    if (u.charged) {
      calls += u.samples;
      qty += u.qty;
    }
  });
  /* Failure analysis, when Debug failures was opened — charged once. */
  const analysis = run.analysis?.credits || 0;
  /* qty: minutes for a voice run, tokens for a text one — the meter it bills. */
  return {
    sim,
    evals: ev,
    analysis,
    calls,
    credits: sim + ev + analysis,
    qty,
    unit: voice ? "minutes" : "tokens",
  };
};

/** Every finished run this period, priced. */
export const usageEntries = (state, { since = null } = {}) => {
  const out = [];
  Object.entries(state?.byEnv || {}).forEach(([envId, es]) => {
    const env = envOf(state, envId);
    if (!env) return;
    (es.runs || [])
      .filter(
        (r) =>
          r.finishedAt &&
          !r.pending &&
          (!since || Date.parse(r.finishedAt) >= since),
      )
      .forEach((run) => {
        const priced = priceRun(env, es, run);
        if (!priced) return;
        const { sim, evals: ev, analysis, calls, qty, unit } = priced;
        out.push({
          id: run.id,
          envId,
          environment: env.name,
          agent: `${es.agent?.name || env.agentName || "Agent"} · ${run.agentVersion || "—"}`,
          project: projectOf(env),
          user: userOf(run),
          label: `Run ${run.ordinal || ""}`.trim(),
          finishedAt: run.finishedAt,
          calls,
          sim,
          evals: ev,
          analysis,
          credits: sim + ev + analysis,
          qty,
          unit,
          stopped: run.budget?.stoppedBy || null,
        });
      });
    /* Improvements, finished or stopped: what each spent past its first run
       — that run is a row of its own — the trials, held-out check, baseline,
       must-pass and any coding-agent attempts, as its Cost tab itemizes. */
    (es.optimizations || [])
      .filter((o) => o.status !== OPT_STATUS.RUNNING && o.status !== "draft")
      .forEach((o) => {
        const at = o.completedAt || o.createdAt;
        if (!at || (since && Date.parse(at) < since)) return;
        const parts = improvementSpendParts(o, env.surface).filter(
          (p) => p.id !== "reproduce",
        );
        const usd = parts.reduce((n, p) => n + (p.usd || 0), 0);
        if (!usd) return;
        const credits = creditsForUsd(usd);
        out.push({
          id: o.id,
          kind: "improvement",
          envId,
          environment: env.name,
          agent: `${es.agent?.name || env.agentName || "Agent"} · ${o.baseAgentVersion || "—"}`,
          project: projectOf(env),
          user: o.startedBy || "Not recorded",
          label: o.name || "Improvement",
          finishedAt: at,
          calls: parts.reduce((n, p) => n + (p.conversations || 0), 0),
          sim: 0,
          evals: 0,
          analysis: 0,
          improvement: credits,
          credits,
          qty: 0,
          unit: ["voice", "multi"].includes(env?.surface) ? "minutes" : "tokens",
          stopped:
            o.status === OPT_STATUS.STOPPED
              ? "stopped"
              : o.stoppedAtBudget
                ? "at limit"
                : null,
        });
      });
  });
  return out.sort(
    (a, b) => Date.parse(b.finishedAt) - Date.parse(a.finishedAt),
  );
};

/** Rows for one grouping: { key, runs, calls, credits, perCall, entries }. */
export const groupUsage = (entries, by) => {
  if (by === "action") {
    const calls = entries.reduce((a, e) => a + e.calls, 0);
    const rows = [
      {
        key: "Simulated caller and actors",
        credits: entries.reduce((a, e) => a + e.sim, 0),
      },
      { key: "Evaluations", credits: entries.reduce((a, e) => a + e.evals, 0) },
      {
        key: "Failure analysis",
        credits: entries.reduce((a, e) => a + (e.analysis || 0), 0),
      },
      {
        key: "Improvement searches",
        credits: entries.reduce((a, e) => a + (e.improvement || 0), 0),
      },
      { key: "Seeded world and tools", credits: 0, note: "included" },
    ];
    return rows.map((r) => ({
      ...r,
      runs: entries.length,
      calls,
      perCall: calls ? r.credits / calls : 0,
      entries,
    }));
  }
  const map = new Map();
  entries.forEach((e) => {
    const k = e[by];
    const row = map.get(k) || {
      key: k,
      runs: 0,
      calls: 0,
      credits: 0,
      entries: [],
    };
    row.runs += 1;
    row.calls += e.calls;
    row.credits += e.credits;
    row.entries.push(e);
    map.set(k, row);
  });
  return [...map.values()]
    .map((r) => ({ ...r, perCall: r.calls ? r.credits / r.calls : 0 }))
    .sort((a, b) => b.credits - a.credits);
};

/** An itemized CSV of the entries, for export. */
export const usageCsv = (entries) => {
  const head = [
    "kind",
    "name",
    "environment",
    "agent",
    "project",
    "user",
    "finished_at",
    "calls",
    "simulation_credits",
    "eval_credits",
    "analysis_credits",
    "improvement_credits",
    "total_credits",
  ];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = entries.map((e) =>
    [
      e.kind || "run",
      e.label,
      e.environment,
      e.agent,
      e.project,
      e.user,
      e.finishedAt,
      e.calls,
      Math.round(e.sim),
      Math.round(e.evals),
      Math.round(e.analysis || 0),
      Math.round(e.improvement || 0),
      Math.round(e.credits),
    ]
      .map(q)
      .join(","),
  );
  return [head.join(","), ...lines].join("\n");
};

/** The first moment of this month — the period every limit counts from. */
export const monthStart = () => {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).getTime();
};

/* One state, one tally: the run plan, the builder and Settings all ask. */
const monthCache = new WeakMap();
/**
 * This month's credits used, from the ledger: { total, byProject, byUser }.
 * The one balance every paid action checks against.
 */
export const monthUsage = (state) => {
  if (!state)
    return {
      total: 0,
      byProject: {},
      byUser: {},
      held: { total: 0, byProject: {}, byUser: {}, runs: 0 },
    };
  if (monthCache.has(state)) return monthCache.get(state);
  const entries = usageEntries(state, { since: monthStart() });
  const out = {
    total: 0,
    byProject: {},
    byUser: {},
    /* Runs still playing hold their limit until they settle, so two runs
       started together can't both spend what's left. */
    held: { total: 0, byProject: {}, byUser: {}, runs: 0 },
  };
  entries.forEach((e) => {
    out.total += e.credits;
    out.byProject[e.project] = (out.byProject[e.project] || 0) + e.credits;
    out.byUser[e.user] = (out.byUser[e.user] || 0) + e.credits;
  });
  const recent = Date.now() - 2 * 3600e3;
  Object.entries(state?.byEnv || {}).forEach(([envId, es]) => {
    const env = envOf(state, envId);
    (es.runs || [])
      .filter(
        (r) =>
          !r.finishedAt &&
          r.status === "running" &&
          r.budget?.cap &&
          Date.parse(r.startedAt || 0) >= recent,
      )
      .forEach((r) => {
        const h = out.held;
        const p = projectOf(env);
        const u = userOf(r);
        h.total += r.budget.cap;
        h.byProject[p] = (h.byProject[p] || 0) + r.budget.cap;
        h.byUser[u] = (h.byUser[u] || 0) + r.budget.cap;
        h.runs += 1;
      });
  });
  monthCache.set(state, out);
  return out;
};

/* One environment's runs, priced once per env state: { runId: credits }. */
const envCache = new WeakMap();
export const runCreditsOf = (env, es) => {
  if (!es) return {};
  if (envCache.has(es)) return envCache.get(es);
  const out = {};
  (es.runs || [])
    .filter((r) => r.finishedAt && !r.pending)
    .forEach((r) => {
      const p = priceRun(env, es, r);
      if (p) out[r.id] = p.credits;
    });
  envCache.set(es, out);
  return out;
};

/**
 * The three simulation meters Settings → Usage shows as product cards, read
 * from the same entries as the Cost tab: voice minutes, text tokens, and the
 * judge evals' credits. Each with a per-day series for its chart.
 *   → { voice_sim_minutes, text_sim_tokens, ai_credits, daily: {key: [{date, usage}]} }
 */
export const simMeters = (state, { since = null } = {}) => {
  const out = { voice_sim_minutes: 0, text_sim_tokens: 0, ai_credits: 0 };
  const days = { voice_sim_minutes: {}, text_sim_tokens: {}, ai_credits: {} };
  usageEntries(state, { since }).forEach((e) => {
    const key = e.unit === "minutes" ? "voice_sim_minutes" : "text_sim_tokens";
    const day = String(e.finishedAt).slice(0, 10);
    out[key] += e.qty;
    out.ai_credits += e.evals + (e.analysis || 0);
    days[key][day] = (days[key][day] || 0) + e.qty;
    days.ai_credits[day] =
      (days.ai_credits[day] || 0) + e.evals + (e.analysis || 0);
  });
  const series = (m) =>
    Object.entries(m)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, usage]) => ({ date, usage }));
  return {
    ...out,
    daily: {
      voice_sim_minutes: series(days.voice_sim_minutes),
      text_sim_tokens: series(days.text_sim_tokens),
      ai_credits: series(days.ai_credits),
    },
  };
};

/* The plan's monthly free tier is used first, by the runs that finished
   first this month — each run's share of it is a known adjustment on its
   Cost tab, and the same amount Settings → Usage shows as free tier.
   → { [runId]: { credits, units, unit, evalCredits } } */
const freeCache = new WeakMap();
export const freeTierByRun = (state) => {
  if (!state) return {};
  if (freeCache.has(state)) return freeCache.get(state);
  const R = RATE_PLAN.rates;
  const pool = { ...FREE_TIER };
  const out = {};
  usageEntries(state, { since: monthStart() })
    .slice()
    .reverse()
    .forEach((e) => {
      const key =
        e.unit === "minutes" ? "voice_sim_minutes" : "text_sim_tokens";
      const units = Math.min(pool[key], e.qty || 0);
      pool[key] -= units;
      const evalCredits = Math.min(
        pool.ai_credits,
        (e.evals || 0) + (e.analysis || 0),
      );
      pool.ai_credits -= evalCredits;
      const simCredits =
        key === "voice_sim_minutes"
          ? units * R.voice_sim_minutes.credits
          : (units / R.text_sim_tokens.per) * R.text_sim_tokens.credits;
      if (simCredits + evalCredits > 0)
        out[e.id] = {
          credits: simCredits + evalCredits,
          units,
          unit: e.unit,
          evalCredits,
        };
    });
  freeCache.set(state, out);
  return out;
};
