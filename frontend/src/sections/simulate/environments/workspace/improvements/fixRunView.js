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

// A fix the engine stopped on its own error leads with what happened and what
// to do next, never the engine's words. Only fix_engine_died's message is the
// engine's own text, kept as details for support; fix_engine_failed's is the
// platform's fixed copy, so it adds nothing.
const ENGINE_STOPPED_CODES = new Set(["fix_engine_died", "fix_engine_failed"]);
const ENGINE_TEXT_CODE = "fix_engine_died";

// Why a fix that is not ready ended, as { text, details }, or null. It names
// Retry only when the fix offers it, as RetryButton does.
export function fixEnding(fix) {
  const { failure } = fix;
  if (failure && ENGINE_STOPPED_CODES.has(failure.code)) {
    const next =
      fix.state === "failed" && fix.retryable
        ? "Retry it, or contact support"
        : "Contact support";
    return {
      text: `The improvement stopped unexpectedly. ${next} with improvement ID ${fix.id}.`,
      details:
        failure.code === ENGINE_TEXT_CODE ? failure.message || null : null,
    };
  }
  const text = failure?.message || fix.report?.summary;
  return text ? { text, details: null } : null;
}

// Ship errors whose message is git's or GitHub's own text, or a raw activation
// outcome code. Ship offers the patch on the GitHub ones, so each says to use
// it; one the server marks retryable keeps Ship as well, so it says to try
// again first. A version that could not become the default keeps Ship.
const PATCH_ERROR_LEAD = new Map([
  [
    "fix_github_patch_failed",
    "This change could not be applied to the repository.",
  ],
  [
    "fix_github_branch_changed",
    "The branch for this pull request changed on GitHub.",
  ],
  ["fix_github_push_failed", "This change could not be pushed to GitHub."],
  // GitHub may have refused it or failed on its side, so this does not say which.
  ["fix_github_pull_request_failed", "GitHub could not open the pull request."],
]);

const NOT_ACTIVATED =
  "This version could not become the default. If the environment is updating, ship again once it finishes.";

export function shipErrorMessage({ code, message, retryable }) {
  const lead = PATCH_ERROR_LEAD.get(code);
  if (lead) {
    const next = retryable ? "Try again, or download" : "Download";
    return `${lead} ${next} the patch to apply it by hand.`;
  }
  if (code === "fix_candidate_not_activated") return NOT_ACTIVATED;
  return message;
}

export function headline(fix) {
  const winner = winnerOf(fix);
  if (fix.state !== "ready" || !winner || !fix.plan) {
    return fixEnding(fix)?.text ?? null;
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

export function checksLine(tiers = []) {
  const counted = tiers.filter((t) => t.status !== "skipped");
  const passed = counted.filter((t) => t.status === "passed").length;
  return `Checks · ${passed} of ${counted.length} passed`;
}

// A trial still being scored has no target rate yet.
export const hasTargetRate = (c) => Array.isArray(c.verdict?.target_pass_rate);

const bestTargetPct = (rated) =>
  Math.max(...rated.map((c) => pct(c.verdict.target_pass_rate[1])));

// The trial a finished fix's Changes and Per scenario tabs open on: the winner,
// else the one that did best on its target scenarios, else the first.
export function reviewTrialOf(fix) {
  const winner = winnerOf(fix);
  if (winner) return winner;
  const trials = [...(fix.candidates || [])].sort(
    (a, b) => a.attempt - b.attempt,
  );
  const rated = trials.filter(hasTargetRate);
  if (!rated.length) return trials[0];
  return rated.reduce((best, c) =>
    c.verdict.target_pass_rate[1] > best.verdict.target_pass_rate[1] ? c : best,
  );
}

// Trials only their must-pass check refused, on cases nobody protected: the
// server ships one on the user's reason. Offered only when nothing won.
export function shipAnywayTrials(fix) {
  if (FIX_LIVE_STATES.has(fix.state) || winnerOf(fix)) return [];
  return (fix.candidates || [])
    .filter((c) => c.override?.allowed)
    .sort((a, b) => a.attempt - b.attempt);
}

// Every tier but must-pass passed; each regression is a must-pass check, as
// "scenario key: check".
export function shipAnywayLine(fix, candidate) {
  const broke = candidate.override.regressions;
  const checks = broke.length === 1 ? "check" : "checks";
  const line = `Trial ${candidate.attempt} passed every check except must-pass: it broke ${broke.length} must-pass ${checks} (${broke.join(", ")}).`;
  const shipped =
    isShipped(fix, candidate) || Boolean(candidate.pull_request_url);
  return shipped ? line : `${line} You can still ship it with a reason.`;
}

function searchCount(plan, candidates) {
  const tried = `${candidates.length} of up to ${plan.max_candidates} trials`;
  const rated = candidates.filter(hasTargetRate);
  if (!rated.length) return tried;
  return `${tried} · best so far ${bestTargetPct(rated)}% on ${plan.target} target scenarios`;
}

function stepLines(id, fix) {
  const { plan, diagnosis } = fix;
  const candidates = fix.candidates || [];
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
  const current = STEPS.findIndex((s) => s.id === fix.step);
  const finished = fix.step === "done";
  return STEPS.map((s, i) => {
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
  rows.push({ label: "Model", value: "unchanged" });
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
