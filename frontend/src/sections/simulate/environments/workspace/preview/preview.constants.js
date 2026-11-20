// Words for the Feature 3 preview pieces. Local preview only: these screens are not in the product designs yet.
export const STATUS_LABELS = {
  supported: "Supported",
  partial: "Partial",
  blocked: "Blocked",
  unavailable: "Unavailable",
  untested: "Untested",
  stale: "Stale",
};

export const BOUNDARY_LABELS = {
  allowed: "Allowed",
  blocked: "Blocked",
  not_enforced: "Not enforced",
};

export const CHECKLIST_LABELS = {
  stable_ids: "Same worker ids across runs",
  delegation_and_return: "A hand-off that returned",
  tool_attribution: "Every tool call names its worker",
  permissions_exercised: "A worker's permissions used",
  timeout_or_cancel: "A worker timed out or was cancelled",
  worker_verdicts: "A check graded per worker",
  unmeasured_without_evidence: "Unmeasured without evidence",
};

export const CHECKLIST_RESULTS = {
  seen: "Seen",
  not_seen: "Not seen yet",
  not_applicable: "Does not apply",
};

export const FILE_ROLE_LABELS = {
  policy: "Policy or reference",
  seed_records: "Seed records",
  evaluator_only: "Evaluator only",
};

export const PREVIEW_COPY = {
  readinessTitle: "Readiness for these versions",
  workersTitle: "Workers",
  blockers: (n) => `${n} blocker${n === 1 ? "" : "s"}`,
  droppedTitle: "Dropped by a rebuild",
  restoredTitle: "Restored under their keys",
  restoredProved: "Proved again",
  restoredUnproved: "Not proved again yet",
  droppedFor: (agent) => (agent ? ` (agent ${agent})` : ""),
  restoreFailed: (why) => `Could not be proved again: ${why}`,
  filesTitle: "Attached files",
  filesHint:
    "An evaluator-only file is never shown to the agent or to the simulated people.",
  noRole: "No role",
  provedWith: (proof) =>
    `Proved with agent ${short(proof.agent_version_id)} on environment ${short(proof.environment_version_id)}, suite ${short(proof.suite_version_id)}`,
  scenarioHint: "What the agent receives: the opening and the situation.",
  personaHint: "What the simulated people are told.",
  privateHint:
    "Private to grading: never shown to the agent or the simulated people.",
};

function short(id) {
  return id ? String(id).slice(0, 8) : "unknown";
}
