export const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export const QUIET = {
  typography: "s3",
  color: "text.secondary",
  wordBreak: "break-word",
};

export const TRIAL_CHANGES_COPY = {
  tab: "World state",
  heading: "What changed in the world",
  noticesHeading: "Worth knowing",
  about:
    "Each participant's attempts in this trial and what the world made of them. Refused and faulted attempts changed nothing.",
  counts: (one) =>
    [
      `${one.attempted} attempted`,
      `${one.committed} committed`,
      one.read ? `${one.read} read` : null,
      one.refused ? `${one.refused} refused` : null,
      one.faulted ? `${one.faulted} faulted` : null,
      one.duplicate ? `${one.duplicate} duplicate` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  committed: (actions) => `Committed: ${actions.join(", ")}`,
  read: (actions) => `Read, changing nothing: ${actions.join(", ")}`,
  answerLost: (actions) =>
    `Committed but answered with a timeout: ${actions.join(", ")}`,
  committedTwice: (action) => `Committed twice: ${action}`,
  blocked: (one) =>
    `Blocked: ${one.host}${one.method ? ` (${one.method})` : ""} ×${one.count}`,
  noChange: "No change: it committed nothing",
  decisionsHeading: "What the actors chose",
  preRunHeading: "Before the run",
  preRunAbout:
    "What the actors wrote in the world before the agent started: the trial's starting point, never graded.",
  verdicts: { approved: "Approved", refused: "Refused" },
  committedAfterRefusal: (name) =>
    `The agent committed the change after ${name} refused it`,
  approvalsHeading: "Approvals",
  // Any part can be missing from an older log.
  approval: ({ request, decision }) =>
    `${request.action || "A request"}, then ${decision.name ? `${decision.name}: ` : ""}${decision.action || "no decision"}`,
  actedBefore: "the agent committed a write before the decision",
  waitedFor: "the agent waited for the decision",
  wroteAfterRefusal: "the agent committed the change anyway",
  afterDecision: (writes) =>
    `then the agent wrote: ${writes.map((one) => `${one.action} (${one.outcome})`).join(", ")}`,
  usageHeading: "Actors' model use",
  usage: (calls, tokens, cost) =>
    `${calls} model call${calls === 1 ? "" : "s"}, ${tokens} tokens, $${cost.toFixed(4)}`,
  usageBy: (who, model, line) =>
    `${who}${model ? ` on ${model}` : ""}: ${line}`,
  usageTotal: (line) => `In all: ${line}, counted in the simulator's usage`,
  // The layout's own labels (tables, captions, toggles).
  callsHeading: "Calls into the world",
  callsHead: {
    who: "Participant",
    attempted: "Attempted",
    committed: "Committed",
    read: "Read",
    refused: "Refused",
    faulted: "Faulted",
    duplicate: "Duplicate",
  },
  committedLabel: "Committed",
  readLabel: "Read only",
  answerLostLabel: "Answered with a timeout",
  blockedLabel: "Blocked outside the world",
  blockedCall: (one) =>
    `${one.host}${one.method ? ` ${one.method}` : ""} ×${one.count}`,
  endStateHint: "The world's records and action log as this trial ended",
  recordsHead: { field: "Field", before: "Before", after: "After" },
  unchangedFields: (n, open) =>
    open
      ? "Hide unchanged fields"
      : `Show ${n} unchanged field${n === 1 ? "" : "s"}`,
  preRunCaption: "The starting point · not graded",
  approvalsHead: {
    request: "Request",
    decision: "Decision",
    agent: "The agent",
  },
  noDecision: "No decision",
  usageHead: {
    who: "Actor",
    model: "Model",
    calls: "Calls",
    tokens: "Tokens",
    cost: "Cost",
  },
  usageTotalLabel: "In all",
  usageNote: "Counted in the simulator's usage",
  empty: "Nothing changed in the world in this trial.",
  notRecorded: "World state was not recorded for this trial.",
  loadFailed:
    "This call's World state could not be loaded. Try again in a moment.",
  unreadable:
    "What changed in the world could not be read for this trial. Checks that needed it read Unmeasured.",
  endStateLead: "The world as this trial ended is kept:",
  endState: "Download end state",
  recordsHeading: "Records before and after",
  recordsEmpty: "No record changed in this trial.",
  recordKinds: { added: "Added", changed: "Changed", removed: "Removed" },
  recordChanged: (fields) => `Changed: ${fields.join(", ")}`,
  before: "Before",
  after: "After",
  notCompared: (table, why) => `Not compared: ${table} (${why})`,
  truncated: (n) => `And ${n} more changes.`,
};
