import { FIX_LIVE_STATES } from "src/api/simulate-environments/fixRuns";

// What the fix page says about a fix, derived from one contract read
// (GET /simulate/api/agent-fix-runs/{id}/). Only simple differences are
// computed here; every number comes from the platform or the engine.

export const STATE_LABEL = {
  queued: "Queued",
  running: "Running",
  stopping: "Stopping",
  ready: "Ready for review",
  no_improvement: "No safe improvement",
  not_reproduced: "Could not reproduce",
  out_of_budget: "Stopped at budget",
  stopped: "Stopped",
  failed: "Failed",
  too_few_cases: "Too few failing cases",
};

export const OUTCOME_LABEL = {
  winner: "Winner",
  finalist: "Not kept",
  rejected: "Rejected",
  pending: "Running",
};

// A trial that passed its screens has only lost once the fix has finished,
// and one still pending when the fix ended had its check runs cancelled.
export const outcomeLabel = (outcome, fixLive) => {
  if (outcome === "finalist" && fixLive) return "Finalist";
  if (outcome === "pending" && !fixLive) return "Did not run";
  return OUTCOME_LABEL[outcome] || outcome;
};

const SURFACE_LABEL = {
  prompt: "Instructions",
  tools: "Tools",
  settings: "Settings",
  code: "Code",
};

const MUST_PASS_RULE =
  "A case that passed every time before must not drop beyond normal noise. A case that fails every run is run again to confirm before it counts.";

// Only a fix whose run had too few failing cases has the first step.
const VARIATIONS_STEP = { id: "variations", label: "Variations" };
const STEPS = [
  { id: "reproduce", label: "Reproduce" },
  { id: "search", label: "Search" },
  { id: "verify", label: "Verify" },
  { id: "done", label: "Ready for review" },
];

export const pct = (x) => Math.round(x * 100);
export const pts = (rate) => Math.round((rate[1] - rate[0]) * 100);
// A rate pools every repeat, so it is not a count of scenarios: Per scenario has those.
const rateChange = ([before, after]) => `${pct(before)}% → ${pct(after)}%`;

export const formatCredits = (n) =>
  `${Number(n ?? 0).toLocaleString("en-US")} credits`;

export function formatMinutes(total) {
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// A fix's winner is graded by the platform's evals after the fix ends, and ships only
// once they finish. The PR body says the same when they could not.
export function gradingNote(fix) {
  if (fix.eval_grading === "pending")
    return "The evals are still grading this trial. Ship it once they finish.";
  if (fix.eval_grading === "incomplete")
    return "The evals could not finish grading this trial; it was not checked by them.";
  return null;
}

export const fixName = (fix) =>
  fix.number == null ? "Improvement" : `Improvement ${fix.number}`;

export function identityLine(fix) {
  const agent = [`agent v${fix.agent.version_number}`, fix.agent.ref]
    .filter(Boolean)
    .join(" · ");
  const version = fix.environment?.version;
  const left = version == null ? agent : `${agent} × environment v${version}`;
  return [left, fix.environment?.name].filter(Boolean).join(" · ");
}

export const winnerOf = (fix) =>
  (fix.candidates || []).find((c) => c.outcome === "winner");

export const isShipped = (fix, candidate) =>
  !!candidate &&
  candidate.agent_version_id === fix.environment?.default_agent_version_id;

const brokeText = (n) => (n ? String(n) : "none");

export function headline(fix) {
  const winner = winnerOf(fix);
  if (fix.state === "no_improvement") {
    return noSafeHeadline(fix) || fix.report?.summary || null;
  }
  if (fix.state !== "ready" || !winner || !fix.plan) {
    return fix.failure?.message || fix.report?.summary || null;
  }
  const { verdict } = winner;
  const broke = brokeText(verdict.must_pass_regressions.length);
  if (verdict.held_back_pass_rate) {
    return `Held-back pass rate ${rateChange(verdict.held_back_pass_rate)}, broke ${broke}.`;
  }
  return `Target pass rate ${rateChange(verdict.target_pass_rate)}, broke ${broke}.`;
}

const rateCard = (id, label, rate, note) => ({
  id,
  label,
  from: pct(rate[0]),
  to: pct(rate[1]),
  delta: pts(rate),
  note,
});

export function verdictCards(fix) {
  const winner = winnerOf(fix);
  if (!winner) return [];
  const { target_pass_rate: target, held_back_pass_rate: held } =
    winner.verdict;
  const targetCard = rateCard(
    "target",
    "Target",
    target,
    "on the scenarios it was tuned on",
  );
  if (!held) return [targetCard];
  return [
    rateCard("held", "Held back", held, "re-run after the pick"),
    targetCard,
    {
      id: "gap",
      label: "Overfit gap",
      value: pct(target[1]) - pct(held[1]),
      note: "between target and held back",
    },
  ];
}

// A check that ran but could not measure reads skipped: “Couldn't measure”. After a
// failed check, the ones left read skipped too, as not run.
export function checksLine(tiers = []) {
  const counted = tiers.filter((t) => t.status !== "skipped");
  const passed = counted.filter((t) => t.status === "passed").length;
  const line = `Checks · ${passed} of ${counted.length} passed`;
  const unmeasured = tiers.length - counted.length;
  if (!unmeasured || counted.some((t) => t.status === "failed")) return line;
  return `${line} · ${unmeasured} couldn't measure`;
}

const CHECK_LABELS = {
  static: "Static checks",
  target: "Target cases",
  "held-back": "Held-back set",
  "must-pass": "Must-pass set",
  repeats: "Repeat runs",
  ceilings: "Cost and latency",
};

// A check's name as the page says it; the engine's ids are never shown. The model
// lock reads as approved models once the fix allows more than one.
export function checkLabel(name, request) {
  if (name === "model-lock") {
    return (request?.allowed_models?.length ?? 0) > 1
      ? "Approved models"
      : "Model unchanged";
  }
  if (CHECK_LABELS[name]) return CHECK_LABELS[name];
  const words = String(name).replace(/[-_]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Each check with its label, and `notRun` with the check that stopped it when it was
// skipped because an earlier one failed. The verdict marks both kinds of skip the same
// way, so the order tells them apart, as in checksLine.
export function describeChecks(tiers = [], request = null) {
  const failed = tiers.findIndex((t) => t.status === "failed");
  return tiers.map((t, i) => {
    const notRun = t.status === "skipped" && failed >= 0 && i > failed;
    return {
      ...t,
      label: checkLabel(t.name, request),
      notRun,
      ...(notRun && {
        notRunBecause: checkLabel(tiers[failed].name, request),
      }),
    };
  });
}

// A rejected trial's checks with the one that rejected it first.
export function rejectionChecks(tiers = [], request = null) {
  const described = describeChecks(tiers, request);
  const rejecting = described.findIndex((t) => t.status === "failed");
  if (rejecting < 0) return described;
  return [described[rejecting], ...described.filter((_, i) => i !== rejecting)];
}

// What stopped the trials, by the check that rejected them, for the no-safe headline:
// `did` follows "both trials"; `missed` follows "neither trial" and `one` the only trial.
const NO_SAFE_WHY = {
  static: { did: "failed the static checks" },
  target: {
    missed: "improved the target cases",
    one: "didn't improve the target cases",
  },
  "held-back": { did: "improved the target cases but not the held-back set" },
  "must-pass": { did: "broke a must-pass case" },
  repeats: {
    missed: "held up over repeat runs",
    one: "didn't hold up over repeat runs",
  },
  ceilings: { did: "went over the cost or latency limit" },
  "model-lock": { did: "called a model outside the approved list" },
};

const whyClause = (name, count, request) => {
  const why = NO_SAFE_WHY[name] || {
    did: `failed the ${checkLabel(name, request).toLowerCase()} check`,
  };
  if (why.did) {
    const who =
      count === 1
        ? "the only trial"
        : count === 2
          ? "both trials"
          : `all ${count} trials`;
    return `${who} ${why.did}`;
  }
  if (count === 1) return `the only trial ${why.one}`;
  return `${count === 2 ? "neither trial" : "no trial"} ${why.missed}`;
};

const rejectedTrials = (fix) =>
  (fix.candidates || [])
    .filter((c) => c.outcome === "rejected")
    .map((c) => ({
      attempt: c.attempt,
      check: (c.verdict?.tiers || []).find((t) => t.status === "failed"),
    }))
    .filter((r) => r.check)
    .sort((a, b) => a.attempt - b.attempt);

// One plain sentence from the trials' own verdicts: what stopped each one. No numbers;
// those are in noSafeNumbers.
export function noSafeHeadline(fix) {
  const trials = rejectedTrials(fix);
  if (!trials.length) return null;
  const names = [...new Set(trials.map((t) => t.check.name))];
  if (names.length === 1) {
    return `No safe improvement: ${whyClause(names[0], trials.length, fix.request)}.`;
  }
  const each = trials
    .map(
      (t, i) =>
        `Trial ${t.attempt}${i === 0 ? " stopped" : ""} at “${checkLabel(t.check.name, fix.request)}”`,
    )
    .join(", ");
  return `No safe improvement: no trial passed every check (${each}).`;
}

// The protected cases the fix could not be held to, in words: each scenario once, by its
// name, or by its key when the engine sent none. Null for a report without them.
export function flakyProtectedText(fix) {
  const rows = fix.report?.flaky_protected || [];
  const names = [
    ...new Map(rows.map((r) => [r.key, r.name || r.key])).values(),
  ];
  if (!names.length) return null;
  const quoted = names.map((n) => `“${n}”`).join(", ");
  return names.length === 1
    ? `1 protected case is flaky on the current version, so this fix wasn't held to it: ${quoted}.`
    : `${names.length} protected cases are flaky on the current version, so this fix wasn't held to them: ${quoted}.`;
}

// The numbers behind the no-safe headline: each trial's rejecting check as the engine
// measured it, and the engine's own summary, word for word.
export function noSafeNumbers(fix) {
  return {
    trials: rejectedTrials(fix).map((t) => ({
      attempt: t.attempt,
      label: checkLabel(t.check.name, fix.request),
      detail: t.check.detail || "",
    })),
    engineNote: fix.report?.summary || "",
  };
}

export const tierIcon = (status) =>
  ({
    passed: "solar:check-circle-linear",
    failed: "solar:close-circle-bold",
    pending: "solar:clock-circle-linear",
    skipped: "solar:minus-circle-linear",
  })[status] || "solar:question-circle-linear";

// A check's icon. One that ran but could not measure has its own, apart from one that
// was not run because an earlier check failed.
export const checkIcon = (check) =>
  check.status === "skipped" && !check.notRun
    ? "solar:eye-closed-linear"
    : tierIcon(check.status);

// A trial still being scored has no target rate yet.
export const hasTargetRate = (c) => Array.isArray(c.verdict?.target_pass_rate);

const bestTargetPct = (rated) =>
  Math.max(...rated.map((c) => pct(c.verdict.target_pass_rate[1])));

function searchCount(plan, candidates) {
  const tried = `${candidates.length} of up to ${plan.max_candidates} trials`;
  const rated = candidates.filter(hasTargetRate);
  if (!rated.length) return tried;
  return `${tried} · best so far ${bestTargetPct(rated)}% on ${plan.target} target scenarios`;
}

const keptVariations = (fix) =>
  Object.keys(fix.request?.variation_parents || {}).length;

const hasVariations = (fix) =>
  fix.step === "variations" ||
  !!fix.request?.variations_pending ||
  keptVariations(fix) > 0;

function variationLines(fix) {
  const { request } = fix;
  if (request.variations_pending) {
    return [
      `Writes variations of the ${request.target_scenario_keys.length} failing cases, then keeps those the current version also fails`,
    ];
  }
  const kept = keptVariations(fix);
  return [
    `Kept ${kept} ${kept === 1 ? "variation" : "variations"} the current version failed: ${request.target_scenario_keys.length} failing cases`,
  ];
}

function stepLines(id, fix) {
  const { plan, diagnosis } = fix;
  const candidates = fix.candidates || [];
  if (id === "variations") return variationLines(fix);
  if (id === "reproduce") {
    return [
      fix.source_run_number != null && fix.agent?.version_number != null
        ? `Starts from Run ${fix.source_run_number} on v${fix.agent.version_number}`
        : null,
      plan
        ? `The current version varied by ${plan.baseline_spread_pts} pts between repeats.`
        : null,
    ].filter(Boolean);
  }
  if (id === "search") {
    const count = plan ? searchCount(plan, candidates) : null;
    const cause = diagnosis?.causes?.[0]
      ? `Starts from its diagnosis: ${diagnosis.causes[0]}`
      : null;
    return [count, cause].filter(Boolean);
  }
  if (id === "verify") {
    if (!plan) return [];
    // Must-pass holds every other scenario too, so it is larger than the
    // protected count the launch form showed; name both.
    const kept = plan.protected
      ? `, including the ${plan.protected} you protected`
      : "";
    return [
      `${plan.held_back} held-back scenarios it never saw and ${plan.must_pass} must-pass scenarios${kept}`,
    ];
  }
  return ["You choose the trial to keep and ship"];
}

export function stepRows(fix) {
  const steps = hasVariations(fix) ? [VARIATIONS_STEP, ...STEPS] : STEPS;
  const current = steps.findIndex((s) => s.id === fix.step);
  const finished = fix.step === "done";
  return steps.map((s, i) => {
    let state = "todo";
    if (finished || i < current) state = "done";
    else if (i === current) state = "now";
    return { ...s, state, lines: stepLines(s.id, fix) };
  });
}

export function heldToRows(fix) {
  const { request } = fix;
  const rows = [
    {
      label: "Allowed changes",
      value: request.surfaces.map((s) => SURFACE_LABEL[s] || s).join(", "),
    },
    { label: "Time cap", value: formatMinutes(request.max_minutes) },
    { label: "Budget", value: formatCredits(request.budget_credits) },
    // While it runs, what it has spent so far; the Cost tab has it after.
    ...(FIX_LIVE_STATES.has(fix.state)
      ? [
          {
            label: "Spent",
            value: `${Number(fix.spent_credits ?? 0).toLocaleString("en-US")} of ${formatCredits(request.budget_credits)}`,
          },
        ]
      : []),
    {
      label: "Protected cases",
      value: `${request.protected_scenario_keys.length} protected`,
    },
  ];
  if (request.locked_paths?.length) {
    rows.push({
      label: "Locked files",
      value: request.locked_paths.join(", "),
    });
  }
  rows.push({ label: "Must-pass", value: MUST_PASS_RULE });
  return [...rows, ...constraintRows(request)];
}

const NOT_TRACED = "can't measure: the agent isn't traced";

// The model lock and the ceilings set at launch. Without the agent's traces the lock and
// the cost limit cannot be checked, so they say so rather than read as held.
function constraintRows(request) {
  const rows = [
    {
      label: "Models",
      value:
        request.observes_models && request.allowed_models?.length
          ? request.allowed_models.join(", ")
          : NOT_TRACED,
    },
  ];
  if (Number.isFinite(request.reply_latency_margin_pct)) {
    rows.push({
      label: "Reply time",
      value: `+${request.reply_latency_margin_pct}%`,
    });
  }
  if (Number.isFinite(request.cost_margin_pct)) {
    const cost = [`+${request.cost_margin_pct}%`];
    if (request.cost_limit_usd != null) {
      cost.push(`at most $${request.cost_limit_usd} a conversation`);
    }
    if (!request.meters_agent_cost) cost.push(NOT_TRACED);
    rows.push({ label: "Agent's cost", value: cost.join(" · ") });
  }
  return rows;
}

// The list's Result cell: the held-back result first, as the design shows it.
export function listResult(fix) {
  const result = verdictResult(fix);
  if (!result || !fix.limited_validation) return result;
  return { ...result, note: `Limited validation · ${result.note}` };
}

function verdictResult(fix) {
  const winner = winnerOf(fix);
  if (fix.state !== "ready" || !winner || !fix.plan) return null;
  const { verdict } = winner;
  const broke = verdict.must_pass_regressions.length;
  if (verdict.held_back_pass_rate) {
    return {
      text: `Held back ${rateChange(verdict.held_back_pass_rate)}`,
      note: broke ? `broke ${broke} held back` : "broke none held back",
      broke,
    };
  }
  return {
    text: `Target ${rateChange(verdict.target_pass_rate)}`,
    note: broke ? `broke ${broke}` : "broke none",
    broke,
  };
}
