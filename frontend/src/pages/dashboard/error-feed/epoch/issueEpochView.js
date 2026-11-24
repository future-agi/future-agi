// What an issue says about its epoch: the label, one sentence, the next action and
// the list icon. Pure: the issue's epoch job, as epochFromIssue.js reads it, plus the
// epoch's own read once one started. Labels come from the epoch page's map.
import {
  OUR_FAILURES,
  STATE_LABEL,
  fixName,
  isShipped,
  noSafeHeadline,
  shipLabel,
  stageLabel,
  stageOf,
  winnerOf,
} from "src/sections/simulate/environments/workspace/improvements/fixRunView";
import { FIX_LIVE_STATES } from "src/api/simulate-environments/fixRuns";
import { paths } from "src/routes/paths";

// The epoch's page, from the epoch's own environment.
export const epochHref = (fix) =>
  paths.dashboard.simulate.environments.improvement(fix.environment_id, fix.id);

// Before the epoch starts: the builder drafts scenarios from the issue's traces, a
// person reviews them, and the current version runs on the kept ones. Blocked:
// drafting ended and none of them can run.
export const PRE_LAUNCH = {
  drafting: "Drafting scenarios",
  review: "Drafts ready",
  blocked: "Nothing to run",
  measuring: "Measuring",
};

// Why a job ended before its epoch started, read as one of the epoch's endings.
// `reason` splits Failed the way an epoch's failure does.
const JOB_ENDINGS = {
  fix_targets_passed: {
    as: "not_reproduced",
    text: "None of the issue's scenarios failed on the current version, so there was nothing to search. It may already be fixed.",
  },
  fix_targets_too_few: {
    as: "too_few_cases",
    text: "Too few of the issue's scenarios failed on the current version to search for a change. Run Epoch again and pick other traces.",
  },
  stopped: {
    as: "stopped",
    text: "This epoch was stopped before it started. Nothing changed.",
  },
  evidence_draft_stale: {
    as: "failed",
    reason: "couldn't start",
    text: "The environment changed after the drafts were reviewed, so they no longer fit it. Nothing changed. Run Epoch again to draft them on the current environment.",
  },
  fix_variations_source_moved: {
    as: "failed",
    reason: "couldn't start",
    text: "The environment's scenarios were saved again while the current version was measured, so the epoch could not start from that run. Nothing changed. Run Epoch again.",
  },
  scenario_unavailable: {
    as: "failed",
    reason: "something only you can change",
    text: "A scenario this epoch needs is no longer in the environment. Run Epoch again to draft it again.",
  },
  fix_run_active: {
    as: "failed",
    reason: "couldn't start",
    text: "Another epoch is running in this environment. Run Epoch again when it ends.",
  },
  issue_epoch_measuring: {
    as: "failed",
    reason: "couldn't start",
    text: "Another issue's epoch was measuring this environment. Run Epoch again when it ends.",
  },
  issue_epoch_dispatch_failed: {
    as: "failed",
    reason: "a problem on our side",
    text: "This epoch could not start because of a problem on our side, not your agent. Nothing changed. Run Epoch again.",
  },
  issue_epoch_baseline_failed: {
    as: "failed",
    reason: "couldn't start",
    text: "The run on the current version did not finish, so the epoch could not start. Nothing changed. Run Epoch again.",
  },
  issue_epoch_timed_out: {
    as: "failed",
    reason: "couldn't start",
    text: "The epoch did not start within 12 hours of Run Epoch, so it was stopped. Nothing changed. Run Epoch again.",
  },
};

// Failed splits four ways (#77). Failures that are ours come from the epoch page's own
// list; the issue job's dispatch failure is mapped in JOB_ENDINGS.
export function failedReason(code) {
  if (code === "fix_deadline") return "ran out of time";
  if (code === "fix_queue_timeout") return "couldn't start";
  if (!code || OUR_FAILURES.has(code)) return "a problem on our side";
  return "something only you can change";
}

const jobEnding = (ending) =>
  JOB_ENDINGS[ending?.code] || {
    as: "failed",
    reason: failedReason(ending?.code),
    text: null,
  };

// One key per state the issue shows: a pre-launch phase, or the epoch's own state.
export function epochKey(status) {
  if (!status) return null;
  if (status.epoch) return status.epoch.state;
  if (status.ending) return jobEnding(status.ending).as;
  return PRE_LAUNCH[status.phase] ? status.phase : null;
}

export const isEpochLive = (status) => {
  const key = epochKey(status);
  return (!!PRE_LAUNCH[key] && key !== "blocked") || FIX_LIVE_STATES.has(key);
};

function reasonOf(status) {
  if (status.epoch) return failedReason(status.epoch.failure?.code);
  return jobEnding(status.ending).reason;
}

export function epochLabel(status) {
  const key = epochKey(status);
  if (!key) return null;
  if (PRE_LAUNCH[key]) return PRE_LAUNCH[key];
  return STATE_LABEL[key] || key;
}

// The label with the failure's kind, for a hover or a badge's title.
export function epochLabelLong(status) {
  const label = epochLabel(status);
  if (epochKey(status) !== "failed") return label;
  return `${label}: ${reasonOf(status)}`;
}

// " after 2 candidates", or "" when only the job's summary of the epoch is read: it
// carries no candidates, so it can't say how many ran.
const whenEnded = (fix) => {
  if (fix.summary) return "";
  const n = (fix.candidates || []).length;
  if (!n) return " before any candidate ran";
  return ` after ${n} ${n === 1 ? "candidate" : "candidates"}`;
};

// What each stage does, in the stage's own word (the epoch page's STAGE_LABEL).
const STAGE_PHRASE = {
  variations: "writing variations of the issue's scenarios",
  reproduce: "reproducing the issue",
  diagnose: "diagnosing the failures",
  search: "proposing changes",
  verify: "checking the best candidate",
};

const sentence = (text) =>
  text
    ? `${text.charAt(0).toUpperCase()}${text.slice(1).replace(/\.?$/, ".")}`
    : "";

// How strong a ready result is (#77): normal says nothing.
function strengthText(fix) {
  const github = fix.agent?.kind === "github";
  if (fix.limited_validation) {
    return github
      ? "Limited validation: too few scenarios were held back to prove it, so its pull request opens as a draft."
      : "Limited validation: too few scenarios were held back to prove it.";
  }
  const winner = winnerOf(fix);
  if (winner && !winner.verdict?.held_back_pass_rate) {
    return "Proven on the targets only: no scenario was held back.";
  }
  return null;
}

function readyText(fix) {
  const name = fixName(fix);
  const winner = winnerOf(fix);
  const lead = winner
    ? `${name} found a change that passed every check.`
    : `${name} is ready for review.`;
  let shipped = null;
  if (winner?.pull_request_url) shipped = "Its pull request is open.";
  else if (isShipped(fix, winner))
    shipped = "It is the environment's default now.";
  return [lead, strengthText(fix), shipped].filter(Boolean).join(" ");
}

function failedText(fix) {
  const name = fixName(fix);
  const code = fix.failure?.code;
  const again = fix.retryable ? " Retry it." : "";
  if (code === "fix_deadline")
    return `${name} ran out of its time limit${whenEnded(fix)}. Nothing changed. Run Epoch again with a longer time limit.`;
  if (code === "fix_queue_timeout")
    return `Too many epochs were running, so ${name} could not start. Nothing changed.${again}`;
  if (!code || OUR_FAILURES.has(code))
    return `${name} stopped because of a problem on our side, not your agent. Nothing changed.${again}`;
  // Only the epoch's own read carries the message; its page says what to change.
  if (!fix.failure?.message)
    return `${name} stopped on something only you can change. Its page says what.`;
  return `${name} stopped on something only you can change: ${sentence(fix.failure.message)}`;
}

function epochText(fix) {
  const name = fixName(fix);
  switch (fix.state) {
    case "queued":
      return `${name} is queued.`;
    case "running":
      return STAGE_PHRASE[stageOf(fix)]
        ? `${name} is ${STAGE_PHRASE[stageOf(fix)]}.`
        : `${name} is running.`;
    case "stopping":
      return `${name} is stopping.`;
    case "ready":
      return readyText(fix);
    case "no_improvement":
      return `${noSafeHeadline(fix) || "No safe improvement: no candidate passed every check."} Nothing changed.`;
    case "not_reproduced":
      return "The issue's scenarios did not fail often enough on the current version. It may already be fixed, or the scenarios miss what triggers it.";
    case "out_of_budget":
      return `${name} reached its budget${whenEnded(fix)}. Nothing changed. Run Epoch again with a larger budget.`;
    case "stopped":
      return `${name} was stopped${whenEnded(fix)}. Nothing changed.`;
    case "too_few_cases":
      return "Too few of the issue's scenarios failed on the current version to search for a change. Run Epoch again and pick other traces.";
    case "failed":
      return failedText(fix);
    default:
      return null;
  }
}

const PRE_LAUNCH_TEXT = {
  drafting:
    "Drafting scenarios from the issue's traces. This is one builder turn, billed as authoring credits.",
  review: "The drafted scenarios are ready. Review them, then run the epoch.",
  blocked:
    "None of the drafted scenarios can run, and each one says why. Plan again and swap the traces that can't run.",
  measuring:
    "Running the reviewed scenarios on the current version. The epoch starts when that run ends.",
};

// One plain sentence: what happened, and what the person can do about it.
export function epochSentence(status) {
  if (!status) return null;
  if (status.epoch) return epochText(status.epoch);
  if (status.ending) {
    const ending = jobEnding(status.ending);
    if (ending.text) return ending.text;
    // A refusal this map does not know: the platform's own message, in its words.
    return sentence(status.ending.message) || "This epoch could not start.";
  }
  return PRE_LAUNCH_TEXT[status.phase] || null;
}

// The next action per state. `kind` tells the page what to render:
//   run      open the Run Epoch drawer on a new plan
//   drawer   open the drawer on the job's current step
//   view     open the epoch page
//   ship     the epoch page's ship button (Open pull request, Make default)
//   retry    the epoch page's retry
const RUN = { kind: "run", label: "Run Epoch" };
const AGAIN = { kind: "run", label: "Run Epoch again" };
const view = (fix) => ({ kind: "view", label: `View ${fixName(fix)}` });

const DRAWER_LABEL = {
  drafting: "View drafts",
  review: "Review drafts",
  blocked: "View drafts",
  measuring: "View progress",
};

export function primaryAction(status) {
  const key = epochKey(status);
  if (!key) return RUN;
  if (PRE_LAUNCH[key]) return { kind: "drawer", label: DRAWER_LABEL[key] };
  const fix = status.epoch;
  if (!fix) return AGAIN;
  if (FIX_LIVE_STATES.has(key)) return view(fix);
  if (key === "ready") {
    const winner = winnerOf(fix);
    // An uploaded agent's winner, once it is the default, has nothing left to ship.
    if (!winner || (isShipped(fix, winner) && !winner.pull_request_url))
      return view(fix);
    return {
      kind: "ship",
      label: winner.pull_request_url ? "View pull request" : shipLabel(fix),
    };
  }
  if (key === "failed" && fix.retryable)
    return { kind: "retry", label: "Retry" };
  return AGAIN;
}

// The actions beside the primary one: the epoch's page, and a new run once it ended.
export function secondaryActions(status) {
  const primary = primaryAction(status);
  const fix = status?.epoch;
  const out = [];
  if (fix && primary.kind !== "view") out.push(view(fix));
  if (epochKey(status) === "measuring")
    out.push({ kind: "stop", label: "Stop" });
  if (
    fix &&
    !isEpochLive(status) &&
    primary.kind !== "run" &&
    epochKey(status) !== "ready"
  ) {
    out.push(AGAIN);
  }
  return out;
}

// The icon after an issue's name in the list, distinct per ending, with its role.
const ICONS = {
  drafting: ["solar:magic-stick-3-linear", "info"],
  review: ["solar:magic-stick-3-linear", "warning"],
  blocked: ["solar:forbidden-circle-linear", "warning"],
  measuring: ["solar:magic-stick-3-linear", "info"],
  queued: ["solar:magic-stick-3-linear", "info"],
  running: ["solar:magic-stick-3-linear", "info"],
  stopping: ["solar:magic-stick-3-linear", "info"],
  ready: ["solar:check-circle-linear", "success"],
  no_improvement: ["solar:shield-cross-linear", "neutral"],
  not_reproduced: ["solar:eye-closed-linear", "neutral"],
  too_few_cases: ["solar:minus-circle-linear", "neutral"],
  out_of_budget: ["solar:pause-circle-linear", "neutral"],
  stopped: ["solar:stop-circle-linear", "neutral"],
  failed: ["solar:danger-triangle-linear", "danger"],
};

export function epochIcon(status) {
  const key = epochKey(status);
  if (!key || !ICONS[key]) return null;
  const [icon, role] = ICONS[key];
  const name = status.epoch ? fixName(status.epoch) : "Epoch";
  const stage = key === "running" && stageLabel(status.epoch);
  const step = stage ? ` · ${stage}` : "";
  return { icon, role, tip: `${name} · ${epochLabelLong(status)}${step}` };
}
