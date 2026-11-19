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
    `Blocked: ${one.host}${one.method ? ` (${one.method})` : ""} ×${one.count ?? 1}`,
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
  // A call still under way: its World state comes in when it ends.
  collecting:
    "This trial is still running: its World state shows here once it ends.",
  // A declared worker the world told apart from the agent.
  worker: (name) => `Participant · ${name}`,
  // An attempt the world refused, kept on the record: what was tried and why the world said no.
  refusedHeading: "Refused attempts",
  refusedAttempt: (one) => [one.action, one.reason].filter(Boolean).join(": "),
  // Records two or more participants named; the key states what the calls named, never a database identity.
  sharedHeading: "Records touched by more than one participant",
  sharedRecord: (one) => `${one.argument} ${one.value}`,
  sharedAction: (action, who) =>
    `${who} · ${action.tool} · ${action.outcome}${action.write === true ? " (write)" : ""}`,
  committedWrites: (n) =>
    `Committed writes: ${n} (tools with recorded effects)`,
  sharedTruncated:
    "More records not shown: this trial's record reached its size limit",
  // Each record's served `truncation`; a cut list of records ("records") is said once, for the trial.
  sharedRecordCut: {
    actions:
      "More calls on this record not shown: its record reached its size limit",
    unknown:
      "More records or calls may not be shown: this trial's record reached its size limit",
  },
  committedLabel: "Committed",
  readLabel: "Read only",
  answerLostLabel: "Answered with a timeout",
  blockedLabel: "Blocked outside the world",
  blockedCall: (one) =>
    `${one.host}${one.method ? ` ${one.method}` : ""} ×${one.count ?? 1}`,
  endStateHint: "The world's records and action log as this trial ended",
  recordsHead: { field: "Field", before: "Before", after: "After" },
  unchangedFields: (n, open) =>
    open
      ? "Hide unchanged fields"
      : `Show ${n} unchanged field${n === 1 ? "" : "s"}`,
  preRunCaption: "The starting point · not graded",
  preRunToggle: (n, open) =>
    open ? "Hide" : `Show ${n} message${n === 1 ? "" : "s"}`,
  preRunDid: (action) => `Ran ${action}`,
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

/* Repeated names as one entry with a count, in first-call order:
   ["get_deal", "get_deal", "update_deal"] -> ["get_deal ×2", "update_deal"]. */
export const groupNames = (names) => {
  const seen = new Map();
  names.forEach((name) => seen.set(name, (seen.get(name) || 0) + 1));
  return [...seen].map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
};

/* Blocked calls to the same host and method as one entry; an older trial
   sent no count, which means one call. */
export const groupBlocked = (calls) => {
  const seen = new Map();
  calls.forEach((one) => {
    const key = `${one.host}\u0000${one.method || ""}`;
    const had = seen.get(key);
    seen.set(key, {
      ...one,
      count: (had?.count || 0) + (one.count ?? 1),
    });
  });
  return [...seen.values()];
};

/* What a pre-run entry said, when it posted a message. */
export const saidText = (args) =>
  args && (args.text || args.message || args.content || args.body);

const OUTCOME_SENTENCE = {
  committed: "It went through.",
  refused: "The world refused it.",
  faulted: "It faulted.",
  duplicate: "It was already done, so it was not repeated.",
};
const VERDICT_SENTENCE = {
  approved: "They approved the request.",
  refused: "They refused the request.",
};
const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);

/* An actor's choice as plain sentences, without the name (shown beside
   it). Scheduled and decided actions are worded here from their fields;
   any other play keeps the server's own words, as sentences. */
export const decisionSentence = (one) => {
  if (one.play === "mocked" || one.play === "live") {
    const did = one.action
      ? one.play === "mocked"
        ? `Ran ${one.action}, as the scenario scheduled.`
        : `Chose to run ${one.action}.`
      : "Chose to do nothing.";
    return [
      did,
      OUTCOME_SENTENCE[one.outcome],
      VERDICT_SENTENCE[one.verdict],
      one.why && `Why: ${one.why}.`,
    ]
      .filter(Boolean)
      .join(" ");
  }
  const parts = String(one.line || "").split(" · ");
  if (parts[0] === one.name) parts.shift();
  return parts.length ? `${parts.map(capital).join(". ")}.` : "";
};

export const showValue = (value) =>
  value === undefined || value === null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

export const KIND_TONE = {
  added: "success.dark",
  changed: "info.dark",
  removed: "error.main",
};

/* Each participant's calls as counts, one row each, with what it committed
   and read as chips under its row. Attempted, refused and committed are
   never summed into one number. */
/* Attempted and Committed always show; Read, Refused, Faulted and
   Duplicate only when some participant has one, and then only non-zero
   counts are printed. */
export const OPTIONAL_COUNTS = [
  { key: "read", tone: "text.secondary" },
  { key: "refused", tone: "warning.dark" },
  { key: "faulted", tone: "error.main" },
  { key: "duplicate", tone: "error.main" },
];

export const ACTION_TONE = {
  committed: "text.primary",
  read: "text.secondary",
  warning: "warning.dark",
};

// A call's outcome while it is still under way (the server's `queued` and `in_progress`): its World state is not in yet.
export const TRIAL_UNDER_WAY = new Set(["queued", "in_progress"]);

// Records shared between a call's participants are not an actors feature: the tab shows them with actors off too.
export const hasSharedRecords = (trialState) =>
  Boolean(trialState?.shared_records?.length);
