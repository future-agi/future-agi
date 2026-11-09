// Submission states (SPEC §3.1) as the submitter sees them, and their place
// in the four-step pipeline: Received → Automated checks → Maintainer review
// → Published.

export const PIPELINE = [
  { key: "received", label: "Received" },
  { key: "checks", label: "Automated checks" },
  { key: "review", label: "Maintainer review" },
  { key: "published", label: "Published" },
];

// DS v0.2 tokens.json color.status: waiting/running use info, never
// discovery (reserved for AI suggestions); failed checks use danger.
// All Hub status consumers resolve their roles here, not per screen.
export const TONE_ROLES = {
  neutral: "neutral",
  working: "info",
  waiting: "info",
  info: "info",
  primary: "info", // compatibility for existing progress indicators
  attention: "warning",
  success: "success",
  error: "danger",
  stopped: "neutral",
};
export const STATUS = {
  received: { label: "Received", tone: "working", step: 0, state: "active" },
  checking: {
    label: "Checks running",
    tone: "working",
    step: 1,
    state: "active",
  },
  checks_failed: {
    label: "Checks failed",
    tone: "error",
    step: 1,
    state: "failed",
  },
  check_error: {
    label: "Checks could not run",
    tone: "error",
    step: 1,
    state: "failed",
  },
  in_review: { label: "In review", tone: "waiting", step: 2, state: "active" },
  changes_requested: {
    label: "Changes requested",
    tone: "attention",
    step: 2,
    state: "failed",
  },
  rejected: {
    label: "Not accepted",
    tone: "stopped",
    step: 2,
    state: "failed",
  },
  approved: { label: "Published", tone: "success", step: 3, state: "done" },
  withdrawn: { label: "Withdrawn", tone: "stopped", step: 0, state: "stopped" },
  superseded: { label: "Replaced", tone: "stopped", step: 0, state: "stopped" },
};

export function statusMeta(status) {
  return (
    STATUS[status] || {
      label: status || "Unknown",
      tone: "neutral",
      step: 0,
      state: "active",
    }
  );
}

// Check results share the submission roles, including failed/error checks.
export function checkTone(status) {
  const submission = {
    pass: "approved",
    fail: "checks_failed",
    block: "checks_failed",
    error: "check_error",
  }[status];
  return submission
    ? statusMeta(submission).tone
    : status === "flag"
      ? "attention"
      : "neutral";
}

export function pipelineTone(state, status) {
  if (state === "done") return "success";
  if (state === "active" || state === "failed") return statusMeta(status).tone;
  return "neutral";
}

// For each pipeline step: done | active | failed | todo | stopped.
export function pipelineStates(status) {
  const meta = statusMeta(status);
  return PIPELINE.map((step, index) => {
    if (meta.state === "stopped") return index === 0 ? "stopped" : "todo";
    if (index < meta.step) return "done";
    if (index > meta.step) return "todo";
    return meta.state === "done" ? "done" : meta.state;
  });
}

export const WITHDRAWABLE = ["received", "checking", "in_review"];

export const TIMELINE_LABEL = {
  "submission.received": "Received",
  "submission.withdrawn": "Withdrawn",
  "submission.superseded": "Replaced by a newer upload",
  "check.claimed": "Automated checks started",
  "check.passed": "Automated checks passed",
  "check.failed": "Automated checks found problems",
  "check.errored": "Automated checks could not run",
  "review.approved": "Approved and published",
  "review.rejected": "Not accepted",
  "review.changes_requested": "Changes requested",
};
