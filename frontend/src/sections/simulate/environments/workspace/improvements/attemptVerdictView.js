import { FIX_LIVE_STATES } from "src/api/simulate-environments/fixRuns";

import { checkLabel } from "./fixRunView";

// What the Epoch page says about an attempt from a person's own coding agent, from
// its verdict read (GET agent-fix-runs/{id}/verdict/) alone: never from the Epoch's
// own candidate verdict, which a signed-in read gives with held-back numbers. The
// engine's detail is never shown; each check reads in plain words from its status.

const STATIC_FAILED =
  "The change did not apply, was too large, or changed a locked file.";

// The checks on cases the coding agent was not given read as a status only.
const PLAIN = {
  static: {
    passed: "Applies cleanly and changes no locked file.",
    failed: STATIC_FAILED,
  },
  target: {
    passed: "A clear gain on the failing cases your agent was given.",
    failed: "Not a clear gain on the failing cases your agent was given.",
  },
  "held-back": {
    passed: "Passed on cases your agent wasn't given.",
    failed: "Did not pass on cases your agent wasn't given.",
  },
  repeats: {
    passed: "Held up over the repeat runs.",
    failed: "Did not hold up over the repeat runs.",
  },
  ceilings: {
    passed: "Within the reply-time and cost limits.",
    failed: "Over a reply-time or cost limit.",
  },
};

function mustPassLine(status, broke) {
  if (status === "passed") return "No case that passed before broke.";
  if (!broke) return "A case that passed before broke.";
  return `${broke} ${broke === 1 ? "case" : "cases"} that passed before broke.`;
}

function checkLine(tier, verdict) {
  if (tier.status === "pending") return "Not run yet.";
  if (tier.name === "must-pass") {
    return mustPassLine(tier.status, verdict.must_pass_regressions.length);
  }
  const said = PLAIN[tier.name]?.[tier.status];
  if (said) return said;
  return tier.status === "passed" ? "Passed." : "Did not pass.";
}

// Each check as CheckLine reads it. A check skipped after a failed one was not run,
// and says which check stopped it; any other skip ran but could not measure (an agent
// whose cost is not metered skips its cost check), and CheckLine says so.
export function attemptChecks(verdict, request) {
  const tiers = verdict?.tiers || [];
  const failed = tiers.findIndex((t) => t.status === "failed");
  return tiers.map((tier, i) => {
    const label = checkLabel(tier.name, request);
    if (tier.status !== "skipped") {
      return {
        name: tier.name,
        status: tier.status,
        label,
        detail: checkLine(tier, verdict),
      };
    }
    const notRun = failed >= 0 && i > failed;
    return {
      name: tier.name,
      status: tier.status,
      label,
      detail: "",
      notRun,
      ...(notRun && {
        notRunBecause: checkLabel(tiers[failed].name, request),
      }),
    };
  });
}

// An attempt the platform refused before any check ran: the engine served its
// static refusal as the summary and uploaded no candidate. Its words stay off the
// page; the check it failed reads in plain words.
export const refusedBeforeChecks = (verdict) =>
  !!verdict &&
  !FIX_LIVE_STATES.has(verdict.state) &&
  !verdict.tiers.length &&
  !!verdict.summary;

export const refusedCheck = (request) => ({
  name: "static",
  status: "failed",
  label: checkLabel("static", request),
  detail: STATIC_FAILED,
});

// The line under the checks while there is nothing, or not everything, to show. How
// an ended attempt came out is the Epoch's own ending, further down the page.
export function attemptNote(verdict) {
  if (!verdict) return null;
  if (FIX_LIVE_STATES.has(verdict.state)) {
    return verdict.tiers.length
      ? "Future AGI is still checking this attempt."
      : "Future AGI is checking this attempt. Its checks show here when they have run.";
  }
  // An attempt that failed with no verdict on its change told its coding agent
  // nothing, so the platform does not count it against the run's attempts.
  const uncounted = verdict.state === "failed" && !verdict.tiers.length;
  const notCounted = uncounted
    ? "It does not use one of the run's attempts."
    : null;
  if (refusedBeforeChecks(verdict)) {
    return [
      "Future AGI checked nothing else.",
      notCounted,
      "Fix the change, then submit it again with fagi epoch submit.",
    ]
      .filter(Boolean)
      .join(" ");
  }
  if (uncounted) {
    return `This attempt stopped before Future AGI checked the change. ${notCounted} Submit it again with fagi epoch submit.`;
  }
  return verdict.tiers.length ? null : "No check ran on this attempt.";
}

// The same rule on an Epoch's own read, for the Epochs list: failed, and no candidate
// has a verdict.
export const isUncountedAttempt = (fix) =>
  fix.state === "failed" && !(fix.candidates || []).some((c) => c.verdict);
