import { FIX_LIVE_STATES } from "src/api/simulate-environments/fixRuns";
import { isOutsideAttempt } from "src/api/simulate-environments/codingAgent";

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
  too_few_cases: "Too few failing scenarios",
};

export const OUTCOME_LABEL = {
  winner: "Winner",
  finalist: "Passed, not picked",
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

// The stages a running epoch goes through, as the design names them, one name
// each on the epoch page and the issue page: never the engine's own stage name.
// Only a fix whose run had too few failing cases has the first one.
export const STAGE_LABEL = {
  variations: "Writing variations",
  reproduce: "Reproducing",
  diagnose: "Diagnosing",
  search: "Proposing",
  verify: "Checking",
};
const stage = (id) => ({ id, label: STAGE_LABEL[id] });
const VARIATIONS_STAGE = stage("variations");
const STAGES = ["reproduce", "diagnose", "search", "verify"].map(stage);

// An attempt from a person's own coding agent only checks the change it was given:
// it never diagnoses or proposes. Its stages come from the same step, in its words.
export const ATTEMPT_STAGE_LABEL = {
  reproduce: "Measuring",
  search: "Checking the change",
  verify: "Checking held-back",
};
const ATTEMPT_STAGES = ["reproduce", "search", "verify"].map((id) => ({
  id,
  label: ATTEMPT_STAGE_LABEL[id],
}));

// The engine has no diagnose step: it diagnoses as its search starts. A search
// with no diagnosis and no candidate yet is still diagnosing; the report's
// diagnosis is optional, so a candidate means it has moved on. The issue job's
// summary of an epoch carries neither, so it reads as the search. An attempt
// never diagnoses.
export function stageOf(fix) {
  if (fix.step !== "search" || fix.summary || isOutsideAttempt(fix))
    return fix.step;
  return fix.diagnosis || (fix.candidates || []).length ? "search" : "diagnose";
}

// The stage a running epoch is in, beside its status.
export const stageLabel = (fix) =>
  (isOutsideAttempt(fix) ? ATTEMPT_STAGE_LABEL : STAGE_LABEL)[stageOf(fix)] ||
  null;

export const pct = (x) => Math.round(x * 100);
export const pts = (rate) => Math.round((rate[1] - rate[0]) * 100);
// A rate pools every repeat, so it is not a count of scenarios: the Scenarios tab has those.
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
    return "Platform evals are still scoring this candidate. You can ship it once they finish.";
  if (fix.eval_grading === "incomplete")
    return "Platform evals did not finish on this candidate, so their results are missing. You can still ship it.";
  return null;
}

export const fixName = (fix) =>
  fix.number == null ? "Epoch" : `Epoch ${fix.number}`;

// What shipping a winner does, as its button says: a GitHub agent's winner opens a
// pull request, and an uploaded agent's becomes the environment's default.
export const shipLabel = (fix) =>
  fix.agent?.kind === "github" ? "Open pull request" : "Make default";

export function identityLine(fix) {
  const agent = [`agent v${fix.agent.version_number}`, fix.agent.ref]
    .filter(Boolean)
    .join(" · ");
  const version = fix.environment?.version;
  // The environment's name is the header's breadcrumb, so it is not said twice.
  return version == null ? agent : `${agent} × environment v${version}`;
}

// Where an epoch came from, in words: its Error Feed issue by title, or the run it
// started from. `kind` says what to link; a deleted one has nothing to link.
export function originOf(fix) {
  if (fix.from_issue) {
    if (!fix.issue) return { kind: null, label: "From a deleted issue" };
    // An issue can have no title yet: it still links.
    const title = (fix.issue.title || "").trim();
    return {
      kind: "issue",
      label: title ? `From issue: ${title}` : "From an issue",
    };
  }
  if (!fix.source_run_id) return { kind: null, label: "From a deleted run" };
  return {
    kind: "run",
    label:
      fix.source_run_number == null
        ? "From its run"
        : `From run: Run ${fix.source_run_number}`,
  };
}

export const winnerOf = (fix) =>
  (fix.candidates || []).find((c) => c.outcome === "winner");

export const isShipped = (fix, candidate) =>
  !!candidate &&
  candidate.agent_version_id === fix.environment?.default_agent_version_id;

const brokeText = (n) =>
  n
    ? `${n} must-pass ${n === 1 ? "scenario" : "scenarios"} broke.`
    : "No must-pass scenario broke.";

const triedText = (fix) => {
  const n = (fix.candidates || []).length;
  return `${n} ${n === 1 ? "candidate" : "candidates"}`;
};

// When an ending came: after how many candidates, or before the first one.
const whenEnded = (fix) =>
  (fix.candidates || []).length
    ? `after ${triedText(fix)}`
    : "before any candidate ran";

// Failures that are ours, not the agent's: the user can only retry.
export const OUR_FAILURES = new Set([
  "fix_engine_failed",
  "fix_engine_died",
  "fix_watch_failed",
  "fix_workflow_lost",
  "fix_dispatch_failed",
  "fix_variations_failed",
  "scheduler_unavailable",
]);

const sentence = (text) =>
  text
    ? `${text.charAt(0).toUpperCase()}${text.slice(1).replace(/\.?$/, ".")}`
    : "";

const SUBMIT_AGAIN = "Submit it again with fagi epoch submit.";

// How an attempt from a person's own coding agent ended, in attempt words: the next
// step is always the coding agent's, never Run Epoch or a retry.
function attemptEndingText(fix) {
  const code = fix.failure?.code;
  switch (fix.state) {
    case "no_improvement":
      return "Your coding agent's change did not pass every check, so nothing changed. The checks above say which. Fix the change, then submit it again with fagi epoch submit.";
    case "not_reproduced":
      return "The run's failing cases did not fail often enough on the current version to check a fix. Nothing changed.";
    case "out_of_budget":
      return "This attempt reached its budget. Nothing changed. Submit it again with a larger budget: fagi epoch submit --budget-credits.";
    case "stopped":
      return "You stopped this attempt. Nothing changed.";
    case "failed":
      if (code === "fix_deadline")
        return `This attempt ran out of its time limit. Nothing changed. ${SUBMIT_AGAIN}`;
      if (!code || code === "fix_queue_timeout" || OUR_FAILURES.has(code))
        return `This attempt stopped because of a problem on our side, not your change. Nothing changed. ${SUBMIT_AGAIN}`;
      return `This attempt stopped on something only you can change: ${sentence(fix.failure.message)}`;
    default:
      return null;
  }
}

// Why a fix that shipped nothing ended, and what the user can do next.
export function endingText(fix) {
  if (isOutsideAttempt(fix)) return attemptEndingText(fix);
  const code = fix.failure?.code;
  if (fix.state === "not_reproduced")
    return "The chosen scenarios did not fail often enough on the current version to fix. Check that they still show the failure, then run a new epoch.";
  if (fix.state === "out_of_budget")
    return `Stopped at the budget ${whenEnded(fix)}. Nothing changed. Run a new epoch with a larger budget.`;
  if (fix.state === "stopped")
    return `You stopped this epoch ${whenEnded(fix)}. Nothing changed.`;
  if (fix.state === "too_few_cases")
    return "The run had too few failing scenarios to fix. Pick more failing scenarios, or let the epoch write variations of them.";
  if (fix.state !== "failed") return null;
  if (code === "fix_deadline")
    return `This epoch ran out of its time limit ${whenEnded(fix)}. Nothing changed. Run a new epoch with a longer time limit.`;
  // A failed epoch can be retried once; after that, the next one starts from the run. A
  // retry takes it once it started: one that never started leaves it, and offers it too.
  const next = "Run a new epoch from the run's Debug failures";
  let again = `It was retried once already. ${next}`;
  if (fix.retryable) again = "Retry it";
  else if (fix.retried_from_id && fix.started_at)
    again = `This epoch was the retry. ${next}`;
  if (code === "fix_queue_timeout")
    return `Too many epochs were running, so this one could not start. Nothing changed. ${again}${fix.retryable ? " in a few minutes" : ""}.`;
  if (!code || OUR_FAILURES.has(code))
    return `This epoch stopped because of a problem on our side, not your agent. Nothing changed. ${again}.`;
  // The engine writes a user-owned failure for the user: what to change, then start again.
  return `This epoch stopped on something only you can change: ${sentence(fix.failure.message)}`;
}

export function headline(fix) {
  const winner = winnerOf(fix);
  if (fix.state === "no_improvement" && isOutsideAttempt(fix)) {
    return endingText(fix);
  }
  if (fix.state === "no_improvement") {
    return (
      noSafeHeadline(fix) ||
      "No safe improvement: no candidate passed every check. Nothing changed."
    );
  }
  if (fix.state !== "ready" || !winner || !fix.plan) return endingText(fix);
  const { verdict } = winner;
  const broke = brokeText(verdict.must_pass_regressions.length);
  if (verdict.held_back_pass_rate) {
    return `Held-back pass rate ${rateChange(verdict.held_back_pass_rate)}. ${broke}`;
  }
  return `Target pass rate ${rateChange(verdict.target_pass_rate)}. ${broke}`;
}

const rateCard = (id, label, rate, note) => ({
  id,
  label,
  from: pct(rate[0]),
  to: pct(rate[1]),
  delta: pts(rate),
  note,
});

// Target minus held back, its sign said in words: the card shows the size.
const gapCard = (value) => ({
  id: "gap",
  label: "Overfit gap",
  value,
  note:
    value > 0
      ? "target did better than held back"
      : value < 0
        ? "held back did better than target"
        : "no gap between target and held back",
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
    gapCard(pct(target[1]) - pct(held[1])),
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
  return `${line} · ${unmeasured} unknown`;
}

const CHECK_LABELS = {
  static: "Change is valid",
  target: "Target scenarios",
  "held-back": "Held-back scenarios",
  "must-pass": "Must-pass scenarios",
  repeats: "Holds over repeats",
  ceilings: "Reply time and cost",
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

const PART_LABELS = {
  "reply latency": "Reply time",
  "agent cost": "Agent's cost",
};
const UNKNOWN_PARTS = {
  "reply latency": "Reply time unknown",
  "agent cost": "Cost unknown",
};
export const UNTRACED_COST =
  "this agent's model calls weren't measured. Epoch measures calls made through the OpenAI or Anthropic Python SDK on its own. For other agents, add traceAI tracing.";

// "3 of 12 finalist conversations …" in the engine's reason: the count the line keeps.
const conversationsOf = (text, cause) => {
  const found = new RegExp(
    `(\\d+) of (\\d+) finalist conversations ${cause}`,
  ).exec(text);
  return found
    ? `${found[1]} of ${found[2]} conversations`
    : "Some conversations";
};

// Why a cost or reply time is unknown, from what the engine found, and what would make it known.
function unknownWhy(part, request) {
  const text = part.detail || "";
  if (part.name === "reply latency")
    return "the conversations recorded no reply times. Run again; if it repeats, check that the agent replies within the turn.";
  if (!request?.meters_agent_cost) return UNTRACED_COST;
  if (/sandbox could not measure/.test(text))
    return `${conversationsOf(text, "have a model call the sandbox")} made a model call that couldn't be measured, such as a call through another SDK or a direct HTTP request. Route that call through the OpenAI or Anthropic SDK, or add traceAI tracing.`;
  if (/baseline not measured|not verified against the baseline/.test(text))
    return "the current version's cost wasn't measured, so there is nothing to compare it with. Run the environment again to measure it.";
  if (/with no cost/.test(text))
    return "a model the agent called has no price. Add a price for it to measure cost.";
  return `${conversationsOf(text, "exported no model call")} returned no model-call record. Run again to measure them.`;
}

// A ceiling's medians, as the verdict was decided on them: the candidate's against the
// current version's, and what the margin and the launch limit allowed.
const CEILING_FORMAT = {
  "reply latency": (n) => `${n.toFixed(2)} s`,
  "agent cost": (n) => `$${Number(n.toFixed(5))}`,
};

function ceilingNumbers(name, numbers) {
  const format = CEILING_FORMAT[name];
  if (!format || numbers?.candidate == null) return "";
  const said = [format(numbers.candidate)];
  if (numbers.baseline != null)
    said.push(`against ${format(numbers.baseline)} on the current version`);
  const bounds = [
    numbers.allowed != null && `allowed up to ${format(numbers.allowed)}`,
    numbers.limit != null && `limit ${format(numbers.limit)}`,
  ].filter(Boolean);
  return `${said.join(" ")}${bounds.length ? `, ${bounds.join(", ")}` : ""}`;
}

// A ceiling's verdict in words: within, over, or unknown with why, with the numbers it was
// decided on. The fix never fails on an unknown number. The engine's own text names the
// cause only for a candidate it failed.
function partDetail(part, request, numbers) {
  const measured = ceilingNumbers(part.name, numbers);
  if (part.status === "passed")
    return measured ? `Within the limit: ${measured}.` : "Within the limit.";
  if (part.status === "failed") {
    if (/^the change removes|^called /.test(part.detail || ""))
      return sentence(part.detail);
    return measured ? `Over the limit: ${measured}.` : "Over the limit.";
  }
  return unknownWhy(part, request);
}

const PART_NUMBERS = { "reply latency": "latency", "agent cost": "cost" };

const CEILING_CARDS = [
  { id: "cost", part: "agent cost", label: "Cost / conversation" },
  { id: "latency", part: "reply latency", label: "Reply time" },
];

const signedPct = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n)}%`;

// The winner's cost and reply time against the current version's, from the
// numbers its ceilings check was decided on. A read without that check, or
// without the part, has no card; a part it could not measure says why.
export function ceilingCards(fix) {
  const tier = (winnerOf(fix)?.verdict?.tiers || []).find(
    (t) => t.name === "ceilings",
  );
  if (!tier) return [];
  return CEILING_CARDS.flatMap(({ id, part: name, label }) => {
    const part = (tier.parts || []).find((p) => p.name === name);
    if (!part) return [];
    const numbers = tier[PART_NUMBERS[name]];
    if (part.status === "skipped") {
      return [
        {
          id,
          label,
          value: "Not measured",
          note: sentence(unknownWhy(part, fix.request)),
        },
      ];
    }
    if (numbers?.candidate == null || !numbers?.baseline) {
      return [
        {
          id,
          label,
          value:
            part.status === "failed" ? "Over the limit" : "Within the limit",
          note: "against the current version",
        },
      ];
    }
    const format = CEILING_FORMAT[name];
    const change = Math.round(
      ((numbers.candidate - numbers.baseline) / numbers.baseline) * 100,
    );
    return [
      {
        id,
        label,
        value: signedPct(change),
        note: `${format(numbers.candidate)} against ${format(numbers.baseline)} on the current version`,
      },
    ];
  });
}

function describePart(part, request, tier) {
  const label = PART_LABELS[part.name] || part.name;
  if (part.status !== "skipped")
    return {
      ...part,
      label,
      detail: partDetail(part, request, tier?.[PART_NUMBERS[part.name]]),
    };
  return {
    ...part,
    label,
    unknown: UNKNOWN_PARTS[part.name] || "Unknown",
    detail: sentence(unknownWhy(part, request)),
  };
}

const rateLine = (rate, gain, tail = "") =>
  rate
    ? `${rateChange(rate)}${tail}, ${gain ? "a clear gain" : "not a clear gain"}.`
    : gain
      ? `A clear gain${tail}.`
      : `Not a clear gain${tail}.`;

// One tier's result in plain words, from the verdict's own numbers. The engine's detail
// (its statistics) is never shown.
function tierLine(tier, verdict, plan, request) {
  const passed = tier.status === "passed";
  if (tier.status === "pending")
    return "Runs only if this candidate is picked.";
  switch (tier.name) {
    case "static":
      return passed
        ? "Applies cleanly and edits only allowed files."
        : "The change did not apply, was too large, or edited a locked file.";
    case "target":
      return rateLine(verdict?.target_pass_rate, passed);
    case "held-back":
      return rateLine(
        verdict?.held_back_pass_rate,
        passed,
        request?.outside_candidate
          ? " on scenarios your coding agent wasn't given"
          : " on scenarios the epoch never saw",
      );
    case "must-pass": {
      const broke = (verdict?.must_pass_regressions || []).length;
      if (!passed)
        return broke ? brokeText(broke) : "A must-pass scenario broke.";
      const kept = plan?.protected
        ? `, including the ${plan.protected} you protected`
        : "";
      return plan?.must_pass
        ? `None of the ${plan.must_pass} must-pass scenarios broke${kept}.`
        : "No must-pass scenario broke.";
    }
    case "repeats":
      return passed
        ? "Held up over the repeat runs."
        : "Did not hold up over the repeat runs.";
    case "ceilings":
      if (passed) return "Within the reply-time and cost limits.";
      return tier.status === "failed" ? "Over a limit." : "";
    case "model-lock":
      if (passed) {
        const models = request?.allowed_models || [];
        return models.length
          ? `Called only ${models.join(", ")}.`
          : "Called only allowed models.";
      }
      if (tier.status === "failed") {
        if (/removes the agent's tracing/.test(tier.detail || ""))
          return "The change removes the agent's tracing, so its model calls can't be checked.";
        if (/names the model|changes the model/.test(tier.detail || ""))
          return "The change names a different model.";
        return "Called a model outside the approved list.";
      }
      return request?.observes_models
        ? "Some conversations sent no traces. The change names no new model."
        : "This agent sends no traces. The change names no new model.";
    default:
      return passed ? "Passed." : "Did not pass.";
  }
}

// Each check with its label, and `notRun` with the check that stopped it when it was
// skipped because an earlier one failed. The verdict marks both kinds of skip the same
// way, so the order tells them apart, as in checksLine.
export function describeChecks(
  tiers = [],
  request = null,
  verdict = null,
  plan = null,
) {
  const failed = tiers.findIndex((t) => t.status === "failed");
  return tiers.map((t, i) => {
    // The engine marks a tier it skipped after a failure; one after a failed must-pass still ran
    // (a final check runs every tier), so a skip there is unknown, not "not run". Only a skip
    // with no detail at all falls back to the order; one with any other detail is unknown.
    const marked = /^not run/.test(t.detail || "");
    const notRun =
      t.status === "skipped" &&
      failed >= 0 &&
      i > failed &&
      (marked || !t.detail);
    return {
      ...t,
      label: checkLabel(t.name, request),
      detail: notRun ? "" : tierLine(t, verdict, plan, request),
      ...(t.status === "skipped" && !notRun && { unknown: "Unknown" }),
      notRun,
      ...(notRun && {
        notRunBecause: checkLabel(tiers[failed].name, request),
      }),
      ...(t.parts && {
        parts: t.parts.map((p) => describePart(p, request, t)),
      }),
    };
  });
}

// A verdict from before the checks were recorded, in words: its target numbers, never its reason.
export const oldVerdictLine = (verdict) =>
  verdict?.target_pass_rate
    ? `Rejected. Target scenarios ${rateChange(verdict.target_pass_rate)}.`
    : "Rejected.";

// A rejected trial's checks with the one that rejected it first.
export function rejectionChecks(
  tiers = [],
  request = null,
  verdict = null,
  plan = null,
) {
  const described = describeChecks(tiers, request, verdict, plan);
  const rejecting = described.findIndex((t) => t.status === "failed");
  if (rejecting < 0) return described;
  return [described[rejecting], ...described.filter((_, i) => i !== rejecting)];
}

// What stopped the trials, by the check that rejected them, for the no-safe headline:
// `did` follows "both trials"; `missed` follows "neither trial" and `one` the only trial.
const NO_SAFE_WHY = {
  static: { did: "failed the static checks" },
  target: {
    missed: "improved the target scenarios",
    one: "didn't improve the target scenarios",
  },
  "held-back": {
    did: "improved the target scenarios but not the held-back scenarios",
  },
  "must-pass": { did: "broke a must-pass scenario" },
  repeats: {
    missed: "held up over repeat runs",
    one: "didn't hold up over repeat runs",
  },
  // The same words as the candidates' group heading.
  ceilings: { did: "went over the reply-time or cost limit" },
  "model-lock": { did: "called a model outside the approved list" },
};

const whyClause = (name, count, request) => {
  const why = NO_SAFE_WHY[name] || {
    did: `failed the ${checkLabel(name, request).toLowerCase()} check`,
  };
  if (why.did) {
    const who =
      count === 1
        ? "the only candidate"
        : count === 2
          ? "both candidates"
          : `all ${count} candidates`;
    return `${who} ${why.did}`;
  }
  if (count === 1) return `the only candidate ${why.one}`;
  return `${count === 2 ? "neither candidate" : "no candidate"} ${why.missed}`;
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
        `Candidate ${t.attempt}${i === 0 ? " stopped" : ""} at “${checkLabel(t.check.name, fix.request)}”`,
    )
    .join(", ");
  return `No safe improvement: no candidate passed every check (${each}).`;
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
    ? `1 protected case is flaky on the current version, so this epoch wasn't held to it: ${quoted}.`
    : `${names.length} protected cases are flaky on the current version, so this epoch wasn't held to them: ${quoted}.`;
}

// The numbers behind the no-safe headline: each trial's rejecting check in plain words,
// from its verdict's own numbers.
export function noSafeNumbers(fix) {
  return {
    trials: rejectedTrials(fix).map((t) => {
      const candidate = fix.candidates.find((c) => c.attempt === t.attempt);
      return {
        attempt: t.attempt,
        label: checkLabel(t.check.name, fix.request),
        detail: tierLine(t.check, candidate?.verdict, fix.plan, fix.request),
      };
    }),
  };
}

// The Candidates tab, grouped as the design groups it: the winner, the ones that
// passed and were not picked, then each check that rejected one, the furthest
// check first, since a candidate that got that far was the closest call. A
// verdict from before checks were recorded has no check to group by.
const GROUP_ORDER = [
  "winner",
  "finalist",
  "must-pass",
  "held-back",
  "repeats",
  "target",
  "ceilings",
  "model-lock",
];
// A check this list does not know sorts after the known ones.
const LAST_GROUPS = { static: 1, rejected: 2, pending: 3 };
const OPEN_GROUPS = new Set(["winner", "finalist", "must-pass"]);

const groupRank = (id) => {
  const at = GROUP_ORDER.indexOf(id);
  return at >= 0 ? at : GROUP_ORDER.length + (LAST_GROUPS[id] ?? 0);
};

const failedTierOf = (c) =>
  (c.verdict?.tiers || []).find((t) => t.status === "failed") || null;

const groupOf = (c) =>
  ["winner", "finalist", "pending"].includes(c.outcome)
    ? c.outcome
    : failedTierOf(c)?.name || "rejected";

// Each rejecting check's group, in plain words: what the candidates in it did.
const GROUP_HEADING = {
  "must-pass": "Broke a must-pass scenario",
  "held-back": "Didn't hold on the held-back scenarios",
  repeats: "Didn't hold over repeat runs",
  target: "Didn't improve the target scenarios",
  ceilings: "Went over the reply-time or cost limit",
  "model-lock": "Called a model outside the approved list",
  static: "The change wasn't valid",
};

// The same groups for an attempt from a person's own coding agent: its one candidate
// is that agent's change, and the held-back cases are the ones it wasn't given.
const ATTEMPT_GROUP_HEADING = {
  "must-pass": "Your coding agent's change broke a must-pass scenario",
  "held-back":
    "Your coding agent's change didn't hold on scenarios it wasn't given",
  repeats: "Your coding agent's change didn't hold over repeat runs",
  target: "Your coding agent's change didn't improve the target scenarios",
  ceilings: "Your coding agent's change went over the reply-time or cost limit",
  "model-lock":
    "Your coding agent's change called a model outside the approved list",
  static: "Your coding agent's change wasn't valid",
};

function groupLabel(id, fix, live) {
  if (["winner", "finalist", "pending"].includes(id))
    return outcomeLabel(id, live);
  if (id === "rejected") return "Rejected";
  const headings = isOutsideAttempt(fix)
    ? ATTEMPT_GROUP_HEADING
    : GROUP_HEADING;
  return headings[id] || `Stopped at “${checkLabel(id, fix.request)}”`;
}

// What stopped a candidate, in the words its verdict's own numbers give.
function candidateResult(c, group, fix, live) {
  if (["winner", "finalist", "pending"].includes(group))
    return { result: outcomeLabel(c.outcome, live), broke: false };
  if (group === "rejected")
    return { result: oldVerdictLine(c.verdict), broke: false };
  if (group === "must-pass") {
    const n = (c.verdict?.must_pass_regressions || []).length;
    return {
      result: n
        ? `Broke ${n} must-pass ${n === 1 ? "scenario" : "scenarios"}`
        : "Broke a must-pass scenario",
      broke: true,
    };
  }
  return {
    result: tierLine(failedTierOf(c), c.verdict, fix.plan, fix.request),
    broke: false,
  };
}

export function candidateGroups(fix) {
  const live = FIX_LIVE_STATES.has(fix.state);
  const hasWinner = !!winnerOf(fix);
  const groups = new Map();
  (fix.candidates || []).forEach((c) => {
    const id = groupOf(c);
    const target = c.verdict?.target_pass_rate;
    const held = c.verdict?.held_back_pass_rate;
    groups.set(id, [
      ...(groups.get(id) || []),
      {
        id: c.id,
        attempt: c.attempt,
        tried: c.summary || "",
        // The candidate's own check run, for its Open run.
        run: c.check_runs?.[0] || null,
        target: Array.isArray(target)
          ? { score: pct(target[1]), delta: pts(target) }
          : null,
        held: Array.isArray(held) ? rateChange(held) : null,
        ...candidateResult(c, id, fix, live),
      },
    ]);
  });
  return [...groups.entries()]
    .sort(([a], [b]) => groupRank(a) - groupRank(b))
    .map(([id, rows]) => ({
      id,
      label: groupLabel(id, fix, live),
      // With no winner, the closest calls were stopped at held back.
      open: OPEN_GROUPS.has(id) || (!hasWinner && id === "held-back"),
      rows: rows.sort(
        (a, b) =>
          (b.target?.score ?? -1) - (a.target?.score ?? -1) ||
          a.attempt - b.attempt,
      ),
    }));
}

// The ending card of an epoch that shipped nothing: its badge, red only when a
// candidate broke a must-pass scenario.
export const endingLabel = (fix) =>
  `${fixName(fix)} · ${STATE_LABEL[fix.state] || fix.state}`;

export const endingBroke = (fix) =>
  fix.state === "no_improvement" &&
  (fix.candidates || []).some(
    (c) => c.outcome === "rejected" && failedTierOf(c)?.name === "must-pass",
  );

// The ending's one sentence. The badge already names a no-safe ending, so its
// headline drops the name.
export function endingSentence(fix) {
  const text = headline(fix);
  if (fix.state !== "no_improvement" || !text) return text;
  return sentence(text.replace(/^No safe improvement: /, ""));
}

const SPENT_ON = {
  stopped: " before it stopped",
  failed: " before it stopped",
  out_of_budget: " before it stopped",
  not_reproduced: " on this check",
  too_few_cases: " reproducing the failing scenarios",
};

// What the ending spent. An epoch that never started, or spent nothing, says
// nothing about it.
export const endingSpent = (fix) =>
  fix.started_at && fix.spent_credits
    ? `Spent ${formatCredits(fix.spent_credits)}${SPENT_ON[fix.state] || ""}.`
    : null;

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
  const tried = `${candidates.length} of up to ${plan.max_candidates} candidates`;
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

// What the diagnosis found: every cause, the first one leading. Before it has
// run, what the stage does.
export const diagnosisCauses = (fix) =>
  (fix.diagnosis?.causes || []).map(sentence);

function diagnoseLines(fix, state) {
  if (!fix.diagnosis) {
    return [
      state === "done"
        ? "No diagnosis was recorded, so the candidates start from the failures"
        : "Reads the failures to find what to change",
    ];
  }
  const [first, ...rest] = diagnosisCauses(fix);
  if (!first)
    return ["Named no single cause, so the candidates start from the failures"];
  return [`Starts from its diagnosis: ${first}`, ...rest];
}

// An attempt's stages: the current version measured, the coding agent's change
// checked on the cases it was given, then on the cases it was not.
function attemptLines(id, fix, state) {
  const done = state === "done";
  if (id === "reproduce") {
    return [
      fix.source_run_number != null
        ? `${done ? "Measured" : "Measures"} the current version on Run ${fix.source_run_number}'s cases`
        : `${done ? "Measured" : "Measures"} the current version on the run's cases`,
    ];
  }
  if (id === "search") {
    return [
      `${done ? "Checked" : "Checks"} that your coding agent's change applies, then runs it on the failing cases it was given`,
    ];
  }
  return [
    `${done ? "Ran" : "Runs"} it on cases your agent wasn't given and on the must-pass cases`,
  ];
}

function stepLines(id, fix, state) {
  const { plan } = fix;
  const candidates = fix.candidates || [];
  if (isOutsideAttempt(fix)) return attemptLines(id, fix, state);
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
  if (id === "diagnose") return diagnoseLines(fix, state);
  if (id === "search") {
    // Before it runs, or before the plan's counts arrive, what the stage does.
    if (state === "todo" || !plan)
      return [
        "Tries changes one at a time; stops early once candidates stop beating the best",
      ];
    return [searchCount(plan, candidates)];
  }
  // Before the plan, what the stage does, without its counts.
  if (!plan)
    return [
      "Checks the best candidate on scenarios it never saw and on the must-pass scenarios",
    ];
  // Must-pass holds every other scenario too, so it is larger than the
  // protected count the launch form showed; name both.
  const kept = plan.protected
    ? `, including the ${plan.protected} you protected`
    : "";
  const held = plan.held_back
    ? `${plan.held_back} held-back scenarios it never saw and `
    : "";
  return [
    `${state === "done" ? "Checked" : "Checks"} the best candidate on ${held}${plan.must_pass} must-pass scenarios${kept}`,
  ];
}

// One row per stage, the current one running. Waiting to start reads Queued in
// the first stage's place, and a stop in progress reads Stopping on the stage
// it interrupts.
export function stepRows(fix) {
  let stages = hasVariations(fix) ? [VARIATIONS_STAGE, ...STAGES] : STAGES;
  if (isOutsideAttempt(fix)) stages = ATTEMPT_STAGES;
  const current = stages.findIndex((s) => s.id === stageOf(fix));
  const finished = fix.step === "done";
  return stages.map((s, i) => {
    let state = "todo";
    if (finished || i < current) state = "done";
    else if (i === current) state = "now";
    let { label } = s;
    if (state === "now" && fix.state === "stopping") label = "Stopping";
    else if (i === 0 && fix.state === "queued") label = "Queued";
    return { ...s, label, state, lines: stepLines(s.id, fix, state) };
  });
}

// Shown only when the platform says it measured this agent's model calls on its
// own; a read without the field shows nothing.
export const MEASURED_NOTICE =
  "Measured automatically: model, tokens and timing only. Prompts and replies are never recorded.";

export const measuredNotice = (fix) =>
  fix.request?.measured_automatically === true ? MEASURED_NOTICE : null;

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

const NOT_TRACED =
  "unknown: this agent's model calls weren't measured. Add traceAI tracing to check them";

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

// Nothing held back: the change was proven on its targets only. Its pull request is
// still a draft, as with Limited validation.
export function provenOnTargetsOnly(fix) {
  return !(fix.request?.held_back_scenario_keys || []).length;
}

// How strong a result is, when too few cases were held back to verify it. A normal
// result has no label.
export function strengthLabel(fix) {
  if (provenOnTargetsOnly(fix)) return "Proven on the targets only";
  return fix.limited_validation ? "Limited validation" : null;
}

// The list's Result cell: the held-back result first, as the design shows it.
export function listResult(fix) {
  const result = verdictResult(fix);
  const strength = strengthLabel(fix);
  if (!result || !strength) return result;
  return { ...result, note: `${strength} · ${result.note}` };
}

function verdictResult(fix) {
  const winner = winnerOf(fix);
  if (fix.state !== "ready" || !winner || !fix.plan) return null;
  const { verdict } = winner;
  const broke = verdict.must_pass_regressions.length;
  if (verdict.held_back_pass_rate) {
    return {
      text: `Held back ${rateChange(verdict.held_back_pass_rate)}`,
      note: broke ? `broke ${broke} must-pass` : "none broke",
      broke,
    };
  }
  return {
    text: `Target ${rateChange(verdict.target_pass_rate)}`,
    note: broke ? `broke ${broke} must-pass` : "none broke",
    broke,
  };
}
