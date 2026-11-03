import { WORKSPACE_TABS } from "../workspace/workspace.constants";

// Build-stage copy for the deriving hero, the building tab rail and the builder
// console. Strings ported verbatim from the designer's DerivedPanels.jsx
// (deriving labels + tab rail) and AssistantConsole.jsx.

// The DerivingAnimation copy, one line per milestone the builder is working on,
// plus the loading branch once all three are done. Verbatim from DerivedPanels.
export const DERIVING_LABEL = {
  understand: "Reading your agent: extracting tools and rules",
  build: "Building the world: seeding data and wiring handlers",
  scenarios: "Writing scenarios: proving each one solvable",
  loading: "Loading the editor for what we derived",
  idle: "Reading your agent…",
  // Preflight already passed inline; this stage is the create call landing.
  creating: "Creating your environment…",
  // Terminal-failed build: the hero reads as failed (the specific reason shows
  // on the failed pipeline step below), not the raw stage name.
  failed: "Build failed: the environment couldn’t be assembled",
  // Terminal-canceled build: the user stopped it, so it reads as stopped rather
  // than failed (no step is at fault).
  canceled: "Build canceled: you stopped it before it finished",
  // Fallback for the source-panel header before a source label is known.
  readingSource: "reading source…",
};

// The tab rail on the building pane: the workspace tabs, rendered pointer-dead.
// Runs, Improvements and Versions are left out — an environment that is still
// building (or failed to build) has no runs, no fixes and no versions yet.
const NOT_WHILE_BUILDING = new Set(["runs", "improvements", "versions"]);
export const BUILDING_TABS = WORKSPACE_TABS.filter((t) => !NOT_WHILE_BUILDING.has(t.id));

export const CONSOLE_COPY = {
  working: "Working on your last message…",
  idle: "Ask, correct, or steer what it builds",
  empty: "Answer the questions below to kick things off. You can also just start typing.",
  placeholder: "Reply to the builder…",
  attach: "Attach a dataset, CSV or file",
  attachAccept: ".csv,.tsv,.json,.jsonl,.xlsx,.txt,.md,.pdf",
  workingDot: "Working…",
  frozen: "Environment is still being built",
  stop: "Stop the current turn",
  collapse: "Collapse chat",
};

export const PIPELINE_CHECKS_COPY = {
  heading: "Setup: building the environment",
  queued: "queued",
};

// The nudge card in the chat: what changed (names only, never values) and
// whether to rebuild the environment or its scenarios.
export const NUDGE_COPY = {
  changed: (label) => `Agent ${label} changed`,
  available: (label) => `Agent ${label} is available`,
  notDefault:
    "Rebuild updates this environment for it and makes it the default agent when the update lands.",
  suiteChanged: (label) => `The environment changed (${label})`,
  source: {
    commit: (from, to) => `Commit ${from} → ${to}`,
    rolledBack: (to) => `Rolled back to commit ${to}`,
    archive: "New upload",
    provider: "Assistant changed",
    phone: "Phone number changed",
    livekit: "Agent name changed",
    other: "Source changed",
  },
  filesMore: (count) => `and ${count} more`,
  filesTruncated: "Only some of the changed files are listed.",
  filesUnavailable: (reason) => `File changes aren't available (${reason})`,
  unavailableReasons: {
    private_repository: "private repository",
    no_commit: "no commit recorded",
    repository_changed: "the repository changed",
    not_linear: "the history isn't linear",
    rate_limited: "GitHub's rate limit was reached",
    github_error: "GitHub couldn't be read",
    archive_missing: "the upload is missing",
    archive_unreadable: "the upload couldn't be read",
    archive_too_large: "the upload is too large",
  },
  promptChanged: "Prompt changed",
  variables: {
    added: "Added",
    removed: "Removed",
    changed: "Changed",
    unknown: "May have changed",
  },
  settingsChanged: (names) => `Settings changed: ${names.join(", ")}`,
  question: {
    environment: "Rebuild the environment?",
    suite: "Rebuild the scenarios?",
  },
  rebuild: "Rebuild",
  rebuildAgain: "Rebuild again",
  rebuildFor: (action, subject) => `${action}: ${subject}`,
  rebuilding: "Starting…",
  rebuildOff: "Rebuilding is turned off for this environment.",
  stop: "Stop",
  stopFor: (subject) => `Stop the update: ${subject}`,
  stopping: "Stopping…",
  stopError: "Couldn't stop the update. Try again.",
  rebuildError: "Couldn't start the update. Try again.",
  usageError: "Your plan's usage limit was reached.",
  // The unsaved edits a Rebuild of this piece would take along or set aside.
  draftLive: (changes, name) =>
    `This also makes the unsaved edits in the draft live: ${changes} change${changes === 1 ? "" : "s"}${name ? `, last edited by ${name}` : ""}.`,
  draftStale: (label) =>
    `Your unsaved edits started from ${label || "an earlier version"}; Rebuild sets them aside.`,
  setAside: (label) =>
    `Your unsaved edits from ${label || "an earlier version"} were set aside.`,
  skip: "Skip",
  skipFor: (subject) => `Skip: ${subject}`,
  skipping: "Skipping…",
  skipError: "Couldn't skip. Try again.",
  // An answer the server refused, by error code.
  errors: {
    nudge_closed: "This update was already answered or closed.",
    nudge_not_found: "This update no longer exists.",
    nudges_disabled: "Rebuild isn't available yet.",
    rebuild_running: "An update is running; try again when it finishes",
    environment_building: "The environment is still being built",
  },
  states: {
    answered_no: "Skipped. The current version stays.",
    no_change: "No change needed",
    failed: "Closed",
    queued: "Waiting for the chat to finish",
    running: "Updating…",
    applied: "Rebuilt",
  },
  queuedBehindInput: "Waiting for your answer in chat",
  applied: (piece, label) =>
    `Rebuilt: ${piece === "suite" ? "scenarios" : "environment"} ${label}`,
  // Why a rebuild ended without a new version, by error code. The unsaved
  // edits are put back as they were.
  failures: {
    stopped_by_user: "Stopped",
    stopped_by_newer: "Stopped by a newer rebuild",
    environment_deleted: "The environment was deleted.",
    no_report: "The builder ended without reporting what it changed.",
    checks_failed: "The update didn't pass its checks.",
    not_runnable: "The updated environment couldn't be prepared to run.",
    turn_failed: "The builder's turn failed.",
    turn_interrupted: "The builder's turn was interrupted.",
    rebuild_not_started: "The update didn't start in time.",
    rebuild_stuck: "The update stopped making progress.",
    landing_failed: "The update couldn't be saved.",
    draft_changed: "Your unsaved edits changed while the update ran.",
    conversation_checkpoint_stale:
      "The environment changed while the update ran, so it wasn't saved.",
    scenario_key_taken:
      "The update added a scenario whose name is already taken.",
  },
  failed: "The update failed.",
  nothingChanged: "Nothing changed; your unsaved edits are as they were.",
  nothingChangedSetAside:
    "Nothing changed. The edits that were set aside stay set aside.",
  inYourEdits: "in your unsaved edits",
  buckets: {
    draftEdits: (name) =>
      name ? `Your unsaved edits (by ${name})` : "Your unsaved edits",
    update: "This update",
    both: "Changed by both",
  },
  reused: (label) => `Same as ${label}, which is the default again.`,
  leftOut: "Left out",
  notChecked: (keys) => `Not checked: ${keys.join(", ")}`,
  agentMoved: (label) => `Agent ${label} is now the default.`,
  agentNotMoved: {
    not_prepared: (label) =>
      `Agent ${label} isn't prepared to run here yet. Make it the default from the Agents panel.`,
    default_moved_by_hand: (label) =>
      `The default agent was changed meanwhile, so agent ${label} didn't become the default.`,
    not_pickable: (label) =>
      `Agent ${label} can't be made the default right now.`,
    other: (label) =>
      `Agent ${label} didn't become the default. Make it the default from the Agents panel.`,
  },
  replacedByAgent: (label) => `Replaced by agent ${label}`,
  replacedByEnvironment: (label) => `Replaced by environment ${label}`,
  replacedSameTrigger: "Updated for a newer environment version",
  replaced: "Replaced by a newer update",
};
