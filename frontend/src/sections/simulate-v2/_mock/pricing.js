/*
  Simulation pricing and budgets (agent-simulation PRD, Sprint 1 · Week 1).

  What a run costs before it starts, in the account's billing unit (credits),
  with everything paid outside Future AGI — the customer's own model keys, a
  voice platform's minutes — kept apart in dollars or marked unknown. Three
  rules hold everywhere:

    · Unknown is never zero. A component with no price reads "unknown"; the
      total never claims to be all-inclusive while one is.
    · A missing platform rate holds paid work. It is never priced at zero.
    · A run limit can only be lower than the admin's per-run limit, from any
      client.

  The rates and the account are mock values; the real ones come from the
  account's active rate plan.
*/
import { evalRailOf } from "./metering";
import { currentAgentVersion } from "./versions";
import { envVersionNameOf } from "./codeRefs";
import {
  AGENT_TOKENS_PER_TRIAL,
  CALL_MINUTES,
  RATE_PLAN,
  TOKENS_PER_TRIAL,
} from "./rates";

/* The rate plan lives in ./rates (no imports), so every model that prices
   work — runs, builds, improvements — reads the same rates. */
export { RATE_PLAN, CALL_MINUTES_RANGE } from "./rates";

/* Provider list prices for the customer's own keys, $ per 1M tokens (blended). */
const MODEL_USD_PER_M = {
  "gpt-4.1": 5,
  "gpt-4o": 6,
  "gpt-4o-mini": 0.4,
  "claude-sonnet-4": 9,
  "gemini-2.5-pro": 6,
  "gemini-2.5-flash": 0.9,
};
/* Voice platforms bill minutes to the customer's own account. */
const PLATFORM_USD_PER_MIN = { vapi: [0.07, 0.12], retell: [0.07, 0.1] };
const PLATFORM_LABEL = {
  vapi: "Vapi",
  retell: "Retell",
  livekit: "LiveKit",
  bland: "Bland",
};
const PRICE_CHECKED = "20 Oct";

/* The workspace's policy: what an admin set. A member can set a lower run
   limit, never a higher one. */
const POLICY_KEY = "fagi.pricing.policy";
const DEFAULT_POLICY = {
  role: "member",
  maxRunCredits: 5000,
  alertAt: 0.8,
  /* Monthly credit limits an admin set. What's been used of each is read
     from the usage ledger, never stored here. null: no limit. Sized for
     runs and improvements together — both count against them. */
  workspaceLimit: 500000,
  projects: {
    Support: { limit: 400000 },
    Billing: { limit: 20000 },
    Travel: { limit: 50000 },
    General: { limit: null },
  },
  /* Per-person limits, by the name a run records; anyone not listed has none. */
  users: {
    Vel: { limit: 80000 },
  },
};
/* An admin's saved changes, kept for this browser (a prototype stand-in for
   the workspace's own policy). */
const loadPolicy = () => {
  try {
    const saved = JSON.parse(
      globalThis.localStorage?.getItem(POLICY_KEY) || "null",
    );
    return saved ? { ...DEFAULT_POLICY, ...saved } : { ...DEFAULT_POLICY };
  } catch {
    return { ...DEFAULT_POLICY };
  }
};
export const WORKSPACE_POLICY = loadPolicy();
export const savePolicy = (patch) => {
  Object.assign(WORKSPACE_POLICY, patch);
  try {
    const { role, ...rest } = WORKSPACE_POLICY;
    globalThis.localStorage?.setItem(POLICY_KEY, JSON.stringify(rest));
  } catch {
    /* Storage blocked: the change lasts until reload. */
  }
};

/* Which project an environment belongs to — by what it tests. */
export const projectOf = (env) => {
  const name = String(env?.name || "").toLowerCase();
  if (/billing|payment|charge/.test(name)) return "Billing";
  if (/airline|travel|rebook|dispatch|pickup/.test(name)) return "Travel";
  if (/support|returns|refund|storefront|quickbite/.test(name))
    return "Support";
  return "General";
};

const credits = (n) => Math.round(n);
export const fmtCredits = (n) =>
  n == null ? "unknown" : `${credits(n).toLocaleString()} credits`;
export const fmtUsd = (n) =>
  n == null ? "unknown" : n < 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(2)}`;
const fmtRange = ([a, b], f) => (a === b ? f(a) : `${f(a)}–${f(b)}`);

const isVoice = (env) => ["voice", "multi"].includes(env?.surface);

/* The workspace's allowance this period, in credits — every metered
   dimension at the active rate, so it reads as one balance. */
/* The workspace's allowance this month, in credits: the limit an admin set,
   and what this month's runs used (from the usage ledger — the same numbers
   Settings → Usage and each run's Cost tab show). */
export const allowanceOf = (used = 0, policy = WORKSPACE_POLICY) => {
  const limit = policy.workspaceLimit ?? null;
  return {
    period: new Date().toLocaleString("en", { month: "long" }),
    limit,
    used: credits(used),
    left: limit == null ? Infinity : credits(limit - used),
  };
};

/**
 * The run plan's estimate.
 *   scope: { scenarios, attempts, versions: [label], surface }
 * → { trials, lines, external, platform: {low, high}, blocked, rateLine }
 * Each line: { id, label, detail, low, high, status: "priced"|"included"|"unpriced" }
 */
export const estimateRunPlan = ({
  env,
  envState,
  scenarios = 0,
  attempts = 1,
  versions = [],
  evals = [],
  runLocation = null,
  missingRate = false,
}) => {
  const versionCount = Math.max(1, versions.length);
  const trials = scenarios * attempts * versionCount;
  const voice = isVoice(env);
  const lines = [];
  const blocked = [];

  /* The simulated caller and the world's actors — the work Future AGI runs. */
  if (voice) {
    const rate = missingRate ? null : RATE_PLAN.rates.voice_sim_minutes;
    if (!rate) {
      blocked.push({
        id: "rate",
        title: "No price is set for voice simulation",
        line: "Paid runs are held until an admin sets the rate. Nothing is charged at zero.",
      });
    }
    const mins = CALL_MINUTES.map((m) => m * trials);
    lines.push({
      id: "sim",
      label: "Simulated caller and actors",
      detail: `${trials} calls × ${CALL_MINUTES[0]}–${CALL_MINUTES[1]} min`,
      low: rate ? mins[0] * rate.credits : null,
      high: rate ? mins[1] * rate.credits : null,
      status: rate ? "priced" : "unpriced",
    });
  } else {
    const rate = missingRate ? null : RATE_PLAN.rates.text_sim_tokens;
    if (!rate) {
      blocked.push({
        id: "rate",
        title: "No price is set for text simulation",
        line: "Paid runs are held until an admin sets the rate. Nothing is charged at zero.",
      });
    }
    const tokens = trials * TOKENS_PER_TRIAL;
    const c = rate ? (tokens / rate.per) * rate.credits : null;
    lines.push({
      id: "sim",
      label: "Simulated caller and actors",
      detail: `${trials} conversations × ~${(TOKENS_PER_TRIAL / 1000).toFixed(0)}K tokens`,
      low: c,
      high: c,
      status: rate ? "priced" : "unpriced",
    });
  }

  /* Managed judges are billed per call; rule-based and own-key judges aren't. */
  const judged = evals.filter((e) => evalRailOf(e).metered);
  if (evals.length) {
    const perTrial = judged.reduce(
      (a, e) => a + (evalRailOf(e).credits || 0),
      0,
    );
    lines.push({
      id: "evals",
      label: "Evaluations",
      detail: judged.length
        ? `${judged.length} managed judge${judged.length === 1 ? "" : "s"} × ${trials} ${voice ? "calls" : "trials"}`
        : `${evals.length} rule-based or own-key`,
      low: perTrial * trials,
      high: perTrial * trials,
      status: judged.length ? "priced" : "included",
    });
  }

  /* The seeded world and its tools answer inside the run at no extra charge. */
  lines.push({
    id: "world",
    label: "Seeded world and tools",
    detail: "Records, tool calls and resets",
    low: 0,
    high: 0,
    status: "included",
  });

  /* Hosted sandbox: measured, not billed while it has no rate. */
  if (runLocation === "hosted") {
    lines.push({
      id: "sandbox",
      label: "Hosted sandbox",
      detail: `~${Math.ceil(trials * 1.5)} min · measured, not billed in beta`,
      low: 0,
      high: 0,
      status: "informational",
    });
  }

  const platform = {
    low: credits(lines.reduce((a, l) => a + (l.low || 0), 0)),
    high: credits(lines.reduce((a, l) => a + (l.high || 0), 0)),
    unknown: lines.some((l) => l.status === "unpriced"),
  };

  /* Paid outside Future AGI — on the customer's own keys and accounts. */
  const external = [];
  const agent = envState?.agent;
  const provider = agent?.values?.provider;
  if (PLATFORM_USD_PER_MIN[provider] && voice) {
    const [lo, hi] = PLATFORM_USD_PER_MIN[provider];
    external.push({
      id: "platform",
      label: `${PLATFORM_LABEL[provider]} minutes`,
      detail: `Billed to your ${PLATFORM_LABEL[provider]} account`,
      low: lo * CALL_MINUTES[0] * trials,
      high: hi * CALL_MINUTES[1] * trials,
      source: `${PLATFORM_LABEL[provider]} list price · checked ${PRICE_CHECKED}`,
    });
  } else if (provider && PLATFORM_LABEL[provider]) {
    external.push({
      id: "platform",
      label: `${PLATFORM_LABEL[provider]} usage`,
      detail: `Billed to your ${PLATFORM_LABEL[provider]} account`,
      low: null,
      high: null,
      source: "No public price to estimate from",
    });
  }
  if (!PLATFORM_USD_PER_MIN[provider]) {
    const model = agent?.values?.model || env?.model || null;
    const perM = model ? MODEL_USD_PER_M[model] : null;
    const usd =
      perM != null ? (trials * AGENT_TOKENS_PER_TRIAL * perM) / 1e6 : null;
    external.push({
      id: "model",
      label: model ? `Your agent's model · ${model}` : "Your agent's model",
      detail: "Billed to your own provider key",
      low: usd,
      high: usd,
      source:
        perM != null
          ? `Provider list price · checked ${PRICE_CHECKED}`
          : "Model or price not known",
    });
  }

  return {
    trials,
    versionCount,
    voice,
    lines,
    external,
    platform,
    blocked,
    rateLine: `${RATE_PLAN.name} · ${RATE_PLAN.version} rates · 1 credit = $${RATE_PLAN.creditUsd.toFixed(2)}`,
  };
};

/**
 * Several run plans read as one — a run group across environments. Trials,
 * lines and the platform total add up; an external cost known on one side
 * and unknown on another reads unknown, never a partial sum.
 */
export const mergeEstimates = (first, rest = []) => {
  if (!rest.length) return first;
  const all = [first, ...rest];
  const sum = (xs) =>
    xs.some((x) => x == null) ? null : xs.reduce((a, x) => a + x, 0);
  const lines = first.lines.map((l) => {
    const same = all.map((e) => e.lines.find((x) => x.id === l.id) || {});
    return {
      ...l,
      detail: `${l.detail} · ${all.length} environments`,
      low: sum(same.map((x) => x.low ?? 0)),
      high: sum(same.map((x) => x.high ?? 0)),
    };
  });
  const external = first.external.map((x) => {
    const same = all.map(
      (e) => e.external.find((y) => y.id === x.id) || { low: null, high: null },
    );
    return {
      ...x,
      low: sum(same.map((y) => y.low)),
      high: sum(same.map((y) => y.high)),
    };
  });
  return {
    ...first,
    trials: all.reduce((a, e) => a + e.trials, 0),
    lines,
    external,
    platform: {
      low: all.reduce((a, e) => a + e.platform.low, 0),
      high: all.reduce((a, e) => a + e.platform.high, 0),
      unknown: all.some((e) => e.platform.unknown),
    },
    blocked: all
      .flatMap((e) => e.blocked)
      .filter((b, i, xs) => xs.findIndex((y) => y.id === b.id) === i),
  };
};

/* The limit a new run starts with: a little over the estimate, never past the
   admin's per-run limit. */
export const defaultRunCap = (estimate, policy = WORKSPACE_POLICY) => {
  const want = Math.ceil(((estimate?.platform?.high || 0) * 1.2) / 50) * 50;
  return Math.min(policy.maxRunCredits, Math.max(50, want));
};

/**
 * Whether the plan can start, and what to say when it can't or only partly.
 * → { canStart, holds: [{title, line}], notes: [string], coveredTrials }
 */
export const checkRunPlan = (
  estimate,
  cap,
  policy = WORKSPACE_POLICY,
  used = 0,
  project = "General",
  projectUsed = 0,
  { user = null, userUsed = 0 } = {},
) => {
  const holds = [...(estimate?.blocked || [])];
  const notes = [];
  const allowance = allowanceOf(used, policy);
  const high = estimate?.platform?.high || 0;
  if (!estimate?.trials)
    holds.push({
      id: "empty",
      title: "Nothing to run",
      line: "Pick at least one scenario.",
    });
  if (cap == null || !(cap > 0))
    holds.push({
      id: "cap",
      title: "Set a run limit",
      line: "Every paid run needs a spending limit.",
    });
  if (cap > policy.maxRunCredits) {
    holds.push({
      id: "policy",
      title: `Over your admin's limit of ${fmtCredits(policy.maxRunCredits)} per run`,
      line: "Lower the run limit, or ask an admin to raise the workspace policy.",
    });
  }
  /* A project with no limit set is bounded only by the workspace. */
  const proj = {
    limit: policy.projects?.[project]?.limit ?? null,
    used: projectUsed,
  };
  const projectLeft =
    proj.limit == null ? Infinity : credits(proj.limit - projectUsed);
  if (Math.min(cap || 0, high) > Math.min(allowance.left, projectLeft)) {
    const which =
      projectLeft < allowance.left
        ? `the ${project} project budget`
        : "your workspace allowance";
    holds.push({
      id: "allowance",
      title: `Not enough left in ${which}`,
      line: `${fmtCredits(Math.min(allowance.left, projectLeft))} left · this run could use up to ${fmtCredits(Math.min(cap || high, high))}.`,
    });
  }
  /* The person's own monthly limit, when an admin set one. */
  const userLimit = user ? policy.users?.[user]?.limit ?? null : null;
  const userLeft = userLimit == null ? Infinity : credits(userLimit - userUsed);
  if (Math.min(cap || 0, high) > userLeft) {
    holds.push({
      id: "user",
      title: "Not enough left in your monthly limit",
      line: `${fmtCredits(userLeft)} of your ${fmtCredits(userLimit)} left · this run could use up to ${fmtCredits(Math.min(cap || high, high))}. Ask an admin to raise it.`,
    });
  }
  /* Crossing a warning line an admin set — said before, not after. */
  const most = Math.min(cap || high, high);
  const at = policy.alertAt || 0.8;
  [
    ["the workspace", allowance.limit, used],
    [`the ${project} project`, proj.limit, projectUsed],
    ["your monthly limit", userLimit, userUsed],
  ].forEach(([who, limit, before]) => {
    if (limit == null || !most) return;
    if (before < limit * at && before + most >= limit * at)
      notes.push(
        `This run can take ${who} past ${Math.round(at * 100)}% of its monthly limit.`,
      );
  });
  /* A limit under the estimate isn't an error: the run stops admitting trials
     when it's reached. Said, so it isn't a surprise. */
  let coveredTrials = estimate?.trials || 0;
  if (cap > 0 && high > cap && estimate?.trials) {
    coveredTrials = Math.max(0, Math.floor((cap / high) * estimate.trials));
    notes.push(
      `Your limit covers about ${coveredTrials} of ${estimate.trials} ${estimate.voice ? "calls" : "trials"} — the rest won't start.`,
    );
  }
  return {
    canStart: holds.length === 0,
    holds,
    notes,
    coveredTrials,
    allowance,
    projectLeft,
    project: { name: project, ...proj },
    user: { name: user, limit: userLimit, left: userLeft },
  };
};

/** The scope line: "5 scenarios × 2 attempts × 2 versions = 20 trials". */
export const scopeLine = ({
  scenarios,
  attempts,
  versionCount,
  trials,
  voice,
}) =>
  [
    `${scenarios} scenario${scenarios === 1 ? "" : "s"}`,
    `${attempts} attempt${attempts === 1 ? "" : "s"}`,
    versionCount > 1 ? `${versionCount} versions` : null,
  ]
    .filter(Boolean)
    .join(" × ") +
  ` = ${trials} ${voice ? "call" : "trial"}${trials === 1 ? "" : "s"}`;

/** The versions a run plan offers: every saved version, current first. */
/**
 * The agent versions a run plan offers, as the rest of the product names
 * them: { label, name ("v8 · main @ b2ca42d" for a code agent), tag
 * ("current" | "draft" | null), more (true: behind "More versions") }.
 * Current first, then drafts, then the most recent; older ones are "more".
 */
export const versionOptionsOf = (envState, all, envId) => {
  const list = all || [];
  const current = currentAgentVersion(envState)?.label;
  const released = new Set((envState?.releases || []).map((r) => r.version));
  const isDraft = (v) => v.status === "draft" && !released.has(v.label);
  const option = (v, tag, more = false) => ({
    label: v.label,
    name: envVersionNameOf(envState, v.label, envId).main,
    tag,
    more,
  });
  const cur = list.find((v) => v.label === current);
  const drafts = list
    .filter((v) => v.label !== current && isDraft(v))
    .reverse();
  const rest = list.filter((v) => v.label !== current && !isDraft(v)).reverse();
  return [
    ...(cur ? [option(cur, "current")] : []),
    ...drafts.map((v) => option(v, "draft")),
    ...rest.map((v, i) => option(v, null, i >= RECENT_VERSIONS)),
  ];
};
const RECENT_VERSIONS = 3;

export const rangeLabel = (low, high, unknown) =>
  unknown
    ? "unknown"
    : low === high
      ? fmtCredits(low)
      : fmtRange([low, high], (n) => credits(n).toLocaleString()) + " credits";

export const usdRange = (low, high) =>
  low == null
    ? "unknown"
    : low === high
      ? `≈ ${fmtUsd(low)}`
      : `≈ ${fmtUsd(low)}–${fmtUsd(high)}`;

/**
 * What one scenario's calls used, from what the run recorded — every attempt
 * (sample) of it — itemized the way the Cost tab shows it:
 *   { samples, qty, unit, simCredits, judgeCalls, evalCredits, credits,
 *     charged, why }
 * `high` is the most it can use, for what a running scenario holds against
 * the limit. A call nothing could be measured on is not charged.
 */
/** A voice call's real length in minutes — what's billed, and what the
    trace and every call duration show. From the call's recorded size. */
export const callMinutesOf = (task) => {
  const steps = task?.steps || [];
  if (steps.length) {
    /* At speaking pace — ~2.6 words a second, a short varied pause before
       each turn and a second between speakers — the same timing the call's
       transcript and recording are drawn at. */
    const secs = steps.reduce((a, s, i) => {
      const words = String(s.text || "")
        .split(/\s+/)
        .filter(Boolean).length;
      return (
        a + (i > 0 ? 0.4 + ((i * 7) % 11) / 10 : 0) + 0.6 + words / 2.6 + 1
      );
    }, 0);
    return secs / 60;
  }
  const share = Math.min(
    1,
    Math.max(0, ((task?.tokens || 1200) - 1200) / 6400),
  );
  return CALL_MINUTES[0] + share * (CALL_MINUTES[1] - CALL_MINUTES[0]);
};

export const usageOfTask = (
  task,
  { voice = false, judgeCredits = 0, judges = 0, repeats = 1 } = {},
  { high = false } = {},
) => {
  const samples = Math.max(1, task?.samples?.length || repeats || 1);
  const share = Math.min(
    1,
    Math.max(0, ((task?.tokens || 1200) - 1200) / 6400),
  );
  let qty;
  let simCredits;
  if (voice) {
    const mins = high ? CALL_MINUTES[1] : callMinutesOf(task);
    qty = mins * samples;
    simCredits = qty * RATE_PLAN.rates.voice_sim_minutes.credits;
  } else {
    const tokens = high
      ? TOKENS_PER_TRIAL * 1.2
      : TOKENS_PER_TRIAL * (0.6 + share * 0.6);
    qty = tokens * samples;
    simCredits =
      (qty / RATE_PLAN.rates.text_sim_tokens.per) *
      RATE_PLAN.rates.text_sim_tokens.credits;
  }
  const judgeCalls = judges * samples;
  const evalCredits = judgeCredits * samples;
  const waived = !high && (task?.status === "unmeasured" || task?.infraFailed);
  return {
    samples,
    qty,
    unit: voice ? "min" : "tokens",
    simCredits: waived ? 0 : simCredits,
    judgeCalls: waived ? 0 : judgeCalls,
    evalCredits: waived ? 0 : evalCredits,
    credits: waived ? 0 : simCredits + evalCredits,
    charged: !waived,
    why: waived ? "Nothing could be measured — not charged" : null,
  };
};

/** Credits one scenario's calls use (see usageOfTask). */
export const creditsOfTask = (task, opts = {}, { high = false } = {}) =>
  task ? usageOfTask(task, opts, { high }).credits : 0;

/** Judge credits per call for a run's evals. */
export const judgeCreditsOf = (evals = []) =>
  evals.reduce((a, e) => a + (evalRailOf(e).credits || 0), 0);

/** Managed judges among a run's evals. */
export const judgesOf = (evals = []) =>
  evals.filter((e) => evalRailOf(e).metered).length;

/** Provider dollars for voice minutes on the agent's platform, or null. */
export const platformUsdOf = (envState, minutes) => {
  const provider = envState?.agent?.values?.provider;
  const r = PLATFORM_USD_PER_MIN[provider];
  if (!r) return null;
  return {
    label: PLATFORM_LABEL[provider],
    low: r[0] * minutes,
    high: r[1] * minutes,
    source: `${PLATFORM_LABEL[provider]} list price · checked ${PRICE_CHECKED}`,
  };
};

/**
 * A training plan's estimate — what `env.rollout(..., dry_run=True)` prints.
 * Rollouts are simulated episodes at the run rates; the held-out evaluation
 * replays held-back scenarios with the environment's judges; training runs
 * on the customer's own trainer, so its cost is theirs and unknown here.
 *   → { parts: [{ id, label, detail, credits|null }], total, episodesInBudget }
 */
/**
 * A world package's validation (`world validate`): reference episodes that
 * must pass, negative and exploit controls that must fail, and the start,
 * reset and isolation checks — episodes priced like a run's, the sandbox
 * measured but not billed in beta. → { total, parts: [{ label, detail, credits }] }
 */
export const estimatePackageValidation = ({
  voice = false,
  reference = 5,
  controls = 10,
} = {}) => {
  const R = RATE_PLAN.rates;
  const perEpisode = voice
    ? ((CALL_MINUTES[0] + CALL_MINUTES[1]) / 2) * R.voice_sim_minutes.credits
    : (TOKENS_PER_TRIAL / R.text_sim_tokens.per) * R.text_sim_tokens.credits;
  const parts = [
    {
      label: "Reference runs",
      detail: `${reference} episodes that must pass`,
      credits: reference * perEpisode,
    },
    {
      label: "Negative and exploit controls",
      detail: `${controls} episodes that must fail`,
      credits: controls * perEpisode,
    },
    {
      label: "Start, reset and isolation",
      detail: "sandbox minutes · measured, not billed in beta",
      credits: 0,
    },
  ];
  return { total: parts.reduce((n, p) => n + p.credits, 0), parts };
};

export const estimateTrainingPlan = ({
  env,
  episodes = 1000,
  heldOut = 100,
  evals = [],
  budget = null,
}) => {
  const voice = isVoice(env);
  const R = RATE_PLAN.rates;
  const perEpisode = voice
    ? ((CALL_MINUTES[0] + CALL_MINUTES[1]) / 2) * R.voice_sim_minutes.credits
    : (TOKENS_PER_TRIAL / R.text_sim_tokens.per) * R.text_sim_tokens.credits;
  const judge = evals.reduce((a, e) => a + (evalRailOf(e).credits || 0), 0);
  const rollouts = episodes * perEpisode;
  const held = heldOut * (perEpisode + judge);
  const total = rollouts + held;
  return {
    perEpisode,
    total,
    episodesInBudget:
      budget && total > budget
        ? Math.max(0, Math.floor((budget - held) / perEpisode))
        : episodes,
    parts: [
      {
        id: "rollouts",
        label: "Rollouts",
        detail: `${episodes.toLocaleString()} episodes × ~${perEpisode.toFixed(1)} credits`,
        credits: rollouts,
      },
      {
        id: "heldOut",
        label: "Held-out evaluation",
        detail: `${heldOut.toLocaleString()} held-back episodes, judged`,
        credits: held,
      },
      {
        id: "training",
        label: "Training",
        detail: "on your trainer — billed by whoever runs it",
        credits: null,
      },
    ],
  };
};
