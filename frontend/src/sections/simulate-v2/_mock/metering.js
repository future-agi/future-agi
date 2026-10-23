/*
  Metering (PRD v6, Story 16) — simulation adds call sites on the platform's
  existing billing rails, plus one new dimension:

    ai_credits         — generated scenarios, managed LLM-judge evals, deep
                         failure analysis
    text_sim_tokens    — text runs, per 1M tokens, tiered
    voice_sim_minutes  — voice runs, per minute, tiered
    sandbox_minutes    — hosted sandbox wall-clock, provision → teardown
                         (new; rate set in billing.yaml, TBD). A customer's own
                         sandbox endpoint meters zero, recorded "daytona-byo".

  Free stays free: connecting, replication, contract derivation, world build,
  manual scenario edits, persona edits, reading results — and rule-based or
  bring-your-own-judge evals. Calls lost to infrastructure (not measured)
  meter nothing.

  Every paid action runs a synchronous pre-check first; a refusal names the
  dimension, current use and limit (the standard limit contract).
*/

export const DIMENSIONS = {
  ai_credits: { label: "AI credits", unit: "credits", short: "credits" },
  text_sim_tokens: { label: "Text simulation", unit: "tokens", short: "tokens" },
  voice_sim_minutes: { label: "Voice simulation", unit: "minutes", short: "min" },
  sandbox_minutes: { label: "Hosted sandbox", unit: "minutes", short: "sandbox min", rateTbd: true },
};

/* Mock list prices — the real ones live in billing.yaml. */
const RATE_USD = {
  ai_credits: 0.01, // per credit
  text_sim_tokens: 4 / 1e6, // per token ($4 per 1M)
  voice_sim_minutes: 0.12, // per minute
  sandbox_minutes: null, // TBD
};

/** A spend in dollars, as the AI credits it comes to at list price. */
export const creditsForUsd = (usd) => (usd == null ? null : Math.round(usd / RATE_USD.ai_credits));
/** "1,200 credits" — how an improvement's budget, estimate or spend is said everywhere. */
export const creditsLabel = (usd) => (usd == null ? "—" : `${creditsForUsd(usd).toLocaleString()} credits`);

/* Credits per managed evaluator call. */
const JUDGE_CREDITS = 2;

/** Whether an eval costs credits per call, and why. */
export const evalRailOf = (evalDef) => {
  const type = String(evalDef?.type || "");
  if (evalDef?.byok) return { metered: false, label: "Free", why: "Judged by your own model key — no credits." };
  if (/LLM judge/i.test(type)) return { metered: true, label: `${JUDGE_CREDITS} credits / call`, credits: JUDGE_CREDITS, why: "A managed LLM judge — metered in AI credits per call." };
  return { metered: false, label: "Free", why: "Rule-based — runs without a model, so it's free." };
};

const isVoice = (env) => ["voice", "multi"].includes(env?.surface);

/**
 * What a run costs before it starts — the estimate the Run button quotes.
 * { lines: [{ dimension, amount, usd }], usd, sandboxTbd }
 */
export const estimateRun = ({ env, scenarios = 0, repeats = 3, evals = [], runLocation = null, byoSandbox = false }) => {
  const calls = scenarios * repeats;
  const lines = [];
  if (isVoice(env)) lines.push({ dimension: "voice_sim_minutes", amount: Math.round(calls * 2.4) });
  else lines.push({ dimension: "text_sim_tokens", amount: calls * 9000 });
  const credits = evals.reduce((a, e) => a + (evalRailOf(e).credits || 0), 0) * calls;
  if (credits) lines.push({ dimension: "ai_credits", amount: credits });
  if (runLocation === "hosted") lines.push({ dimension: "sandbox_minutes", amount: byoSandbox ? 0 : Math.ceil(calls * 1.5), byo: byoSandbox });
  return withUsd(lines);
};

/**
 * What a finished run metered — the consumption line on its record.
 * `tasks` carry tokens / duration; calls not measured meter nothing.
 */
export const meteredOfRun = ({ env, tasks = [], evals = [], runLocation = null, byoSandbox = false }) => {
  const billed = tasks.filter((t) => t.status !== "unmeasured" && !t.infraFailed);
  const lines = [];
  if (isVoice(env)) {
    const secs = billed.reduce((a, t) => a + (t.durationSec || t.duration || 140), 0);
    lines.push({ dimension: "voice_sim_minutes", amount: Math.round(secs / 60) });
  } else {
    lines.push({ dimension: "text_sim_tokens", amount: billed.reduce((a, t) => a + (t.tokens || 0), 0) });
  }
  const credits = evals.reduce((a, e) => a + (evalRailOf(e).credits || 0), 0) * billed.length;
  if (credits) lines.push({ dimension: "ai_credits", amount: credits });
  if (runLocation === "hosted") {
    lines.push({ dimension: "sandbox_minutes", amount: byoSandbox ? 0 : Math.ceil(billed.length * 1.5), byo: byoSandbox });
  }
  return { ...withUsd(lines), excluded: tasks.length - billed.length };
};

const withUsd = (lines) => {
  const priced = lines.map((l) => ({ ...l, usd: RATE_USD[l.dimension] == null ? null : Math.round(l.amount * RATE_USD[l.dimension] * 100) / 100 }));
  return {
    lines: priced,
    usd: Math.round(priced.reduce((a, l) => a + (l.usd || 0), 0) * 100) / 100,
    sandboxTbd: priced.some((l) => l.dimension === "sandbox_minutes" && l.amount > 0),
  };
};

/** "1.2M tokens · 340 credits · 18 sandbox min (rate TBD)" */
export const meterLine = ({ lines = [] }) => lines.map((l) => {
  const d = DIMENSIONS[l.dimension];
  const n = l.dimension === "text_sim_tokens" && l.amount >= 1e5 ? `${(l.amount / 1e6).toFixed(1)}M` : l.amount.toLocaleString();
  if (l.byo) return "0 sandbox min (your sandbox)";
  return `${n} ${d.short}${d.rateTbd && l.amount ? " (rate TBD)" : ""}`;
}).join(" · ");

/* ── the org's usage this period, and the pre-check ─────────────────────── */

/* Mock org: one dimension close to its limit so the limit UX can be shown.
   Voice minutes sit where a default run of every seeded voice environment
   (3 repeats) crosses the 90% budget and warns but still runs, while a big
   run (the voice demo at 6+ repeats, any voice env at 20) is refused. */
export const ORG_USAGE = {
  period: "October",
  dimensions: {
    ai_credits: { current: 41800, limit: 50000, budget: null },
    text_sim_tokens: { current: 38e6, limit: 100e6, budget: null },
    voice_sim_minutes: { current: 4100, limit: 5000, budget: { at: 0.9, action: "warn" } },
    sandbox_minutes: { current: 0, limit: null, budget: null },
  },
};

/**
 * The synchronous pre-check at a paid boundary.
 * { allowed, dimension, current, limit, after, warn } — refused only when the
 * estimate would cross the limit; a budget threshold warns or pauses.
 */
export const checkUsage = (estimate, usage = ORG_USAGE) => {
  for (const l of estimate?.lines || []) {
    const d = usage.dimensions[l.dimension];
    if (!d || d.limit == null || !l.amount) continue;
    const after = d.current + l.amount;
    if (d.budget?.action === "pause" && after >= d.limit * d.budget.at) {
      return { allowed: false, paused: true, dimension: l.dimension, current: d.current, limit: d.limit, after };
    }
    if (after > d.limit) return { allowed: false, dimension: l.dimension, current: d.current, limit: d.limit, after };
  }
  const warn = (estimate?.lines || []).find((l) => {
    const d = usage.dimensions[l.dimension];
    return d?.budget?.action === "warn" && d.limit && d.current + l.amount >= d.limit * d.budget.at;
  });
  return { allowed: true, warn: warn ? { dimension: warn.dimension, ...usage.dimensions[warn.dimension] } : null };
};


/* ── wording the pre-check, and the eval rail per run ────────────────────── */

const amountOf = (dimension, n) => {
  const d = DIMENSIONS[dimension];
  if (dimension === "text_sim_tokens" && n >= 1e5) return `${(n / 1e6).toFixed(1)}M ${d.unit}`;
  return `${Math.round(n).toLocaleString()} ${d?.unit || ""}`.trim();
};

/**
 * The standard limit message — dimension, current use, limit — for a refused
 * pre-check. → { title, line, short } or null when allowed.
 */
export const limitMessage = (check, estimate, usage = ORG_USAGE) => {
  if (!check || check.allowed) return null;
  const d = DIMENSIONS[check.dimension];
  const need = (estimate?.lines || []).find((l) => l.dimension === check.dimension)?.amount ?? (check.after - check.current);
  return {
    dimension: check.dimension,
    title: check.paused ? `${d.label} paused at your budget` : `Over your ${d.label} limit`,
    line: `${Math.round(check.current).toLocaleString()} of ${amountOf(check.dimension, check.limit)} used this ${usage.period}; this run needs about ${amountOf(check.dimension, need)}.`,
    short: `${d.label} · ${Math.round(check.current).toLocaleString()} / ${Math.round(check.limit).toLocaleString()} ${d.short}`,
  };
};

/** The quiet budget notice for a pre-check that warns but allows. */
export const budgetNotice = (check, estimate, usage = ORG_USAGE) => {
  if (!check?.allowed || !check.warn) return null;
  const w = check.warn;
  const d = DIMENSIONS[w.dimension];
  const need = (estimate?.lines || []).find((l) => l.dimension === w.dimension)?.amount || 0;
  const after = w.current + need;
  /* Said so it needs no context: whose usage (the org's), which meter, this
     month's numbers, and why it shows (this run crosses the budget line). */
  const big = w.dimension === "text_sim_tokens" && w.limit >= 1e5;
  const num = (n) => (big ? `${(n / 1e6).toFixed(1)}M` : Math.round(n).toLocaleString());
  const noun = w.dimension === "ai_credits" ? "AI credits" : `${d.label.toLowerCase().replace(/\s+/g, "-")} ${d.unit}`;
  return {
    dimension: w.dimension,
    line: `Your org has used ${num(w.current)} of ${num(w.limit)} ${noun} this month (${usage.period}). This run adds about ${num(need)}, taking it to ${Math.round((after / w.limit) * 100)}% — past your ${Math.round(w.budget.at * 100)}% budget line. It still runs.`,
  };
};

/** AI credits a run's evals cost: per-call credits × scenarios × repeats. */
export const evalCreditsPerRun = ({ evals = [], scenarios = 0, repeats = 3 }) =>
  evals.reduce((a, e) => a + (evalRailOf(e).credits || 0), 0) * scenarios * repeats;

/**
 * AI credits to re-grade a past run from its stored traces and end-states
 * (PRD v7 AC-12.23, AC-16.11): each call's end-state is restored and graded
 * again — 1 credit per call to restore and run code checks, plus the managed
 * judge's credits for each judged sub-goal fixed and each metered eval.
 */
export const estimateRegrade = ({ calls = 0, judgedSubGoals = 0, evals = [] }) => {
  const judge = judgedSubGoals * JUDGE_CREDITS + evals.reduce((a, e) => a + (evalRailOf(e).credits || 0), 0);
  return calls * (1 + judge);
};

/* ── building an environment ─────────────────────────────────────────────── */

/* Writing and proving one scenario at build — the AI credits it takes. */
export const BUILD_CREDITS_PER_SCENARIO = 9;

/**
 * Whether the AI credits left this period cover writing `scenarioCount`
 * scenarios — asked on the connect form, shown on the build screen, and
 * where a build that runs out stops.
 *   → { ok, need, left, affordable, message } — `affordable` is the most scenarios the credits left cover.
 */
export const buildCreditsCheck = (scenarioCount, usage = ORG_USAGE) => {
  const d = usage.dimensions.ai_credits;
  const need = Math.max(0, Math.round(Number(scenarioCount) || 0)) * BUILD_CREDITS_PER_SCENARIO;
  const left = Math.max(0, (d.limit ?? Infinity) - d.current);
  const affordable = Math.floor(left / BUILD_CREDITS_PER_SCENARIO);
  const ok = d.limit == null || need <= left;
  return {
    ok, need, left, affordable,
    message: ok ? null
      : `Writing ${Math.round(Number(scenarioCount)).toLocaleString()} scenarios needs about ${need.toLocaleString()} AI credits; ${left.toLocaleString()} of ${d.limit.toLocaleString()} are left this ${usage.period}.`,
  };
};
