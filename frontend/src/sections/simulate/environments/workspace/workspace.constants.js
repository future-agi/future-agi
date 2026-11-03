// Workspace shell copy and tab metadata, ported from the designer's
// EnvironmentWorkspace.jsx and DerivedPanels.jsx. Strings live here rather than
// inline so the shell, the build-page in-place swap and the tests share them.

// The workspace tabs, in rail order (matching the designer). Overview is first
// and is the default landing tab. Overview and Contract are views of the
// environment itself, so they carry no count badge; Scenarios, Evaluations and
// Runs badge the length of their matching env-state slice. Settings is last.
// The Overview tab id is "overview" (URL reads ?tab=overview), and its panel
// component/folder keep the OverviewPanel name.
export const WORKSPACE_TABS = [
  { id: "overview", label: "Overview", icon: "solar:widget-5-linear" },
  {
    id: "contract",
    label: "Contract",
    icon: "solar:document-text-linear",
    hint: "What this environment is made of: the world runs execute against, the code behind every tool call and grader, and how each run ends.",
  },
  {
    id: "scenarios",
    label: "Scenarios",
    icon: "solar:layers-minimalistic-linear",
    badge: "scenarios",
    hint: "Each scenario is one task your agent has to complete, and carries its own persona.",
  },
  {
    id: "evals",
    label: "Evaluations",
    icon: "solar:shield-check-linear",
    badge: "evals",
    hint: "These decide whether each task passed. Pick them from the library and map their inputs onto what the run produces.",
  },
  { id: "runs", label: "Runs", icon: "solar:play-circle-linear", badge: "runs" },
  // Shown only once the environment has a fix (Fix my agent).
  { id: "improvements", label: "Improvements", icon: "solar:magic-stick-3-linear", badge: "improvements" },
  // Shown only when the environment has versioning switched on.
  { id: "versions", label: "Versions", icon: "solar:history-linear" },
  {
    id: "settings",
    label: "Settings",
    icon: "solar:settings-linear",
    hint: "How this environment was built. Everything here is fixed at build time. Only the name can be changed.",
  },
];

// Setup-gap areas map onto the tab that owns the underlying answer, so a
// blocking gap surfaces as an amber dot on that tab. Only the areas the user
// can resolve inside this workspace are routed; the designer's Sandbox/Tools
// entries point at an Agents tab we do not render, so they are omitted.
export const GAP_AREA_TO_TAB = {
  Contract: "contract",
  Grading: "evals",
};

// Header, overflow-menu and empty-state copy for the workspace shell.
export const WORKSPACE_COPY = {
  back: "All environments",
  live: "Ready",
  buildingLabel: "Building",
  finalizingLabel: "Finalizing",
  failedLabel: "Failed",
  liveTooltip:
    "Environment is ready to run. You can edit via the builder or inline.",
  buildingTooltip: "Environment is still being built…",
  finalizingTooltip: "Wrapping up the build…",
  cancellingLabel: "Cancelling",
  cancellingTooltip: "Stopping the build and cleaning up its sandbox.",
  cancelledLabel: "Cancelled",
  cancelledTooltip: "You cancelled this build.",
  runningTooltip:
    "A simulation is running. Results land under Runs when it finishes.",
  failedTooltip: "This environment failed to build.",
  run: "Run simulation",
  moreActions: "More actions",
  fork: "Fork environment",
  forkHint:
    "Duplicate the world for a different agent or team. Agent + runs reset.",
  deleteEnv: "Delete environment",
  deleteEnvHint:
    "Remove this environment and cancel any live run. This can't be undone.",
  notFound: {
    title: "Environment not found",
    body: "It may have been removed from your workspace.",
    action: "Back to environments",
  },
  // Shown when the environment can't be loaded for a reason other than a clean
  // 404 (a server error, a network failure) — recoverable, so it offers a retry.
  loadError: {
    title: "Couldn’t load this environment",
    body: "Something went wrong fetching it. Retry, or go back to your environments.",
    retry: "Retry",
  },
  // The environment's status loaded but its details (contract, world, agent,
  // counts) did not. Every tab reads those, so none is shown half-filled.
  detailError: {
    title: "Couldn’t load this environment’s details",
    body: "Its contract, world and counts didn’t load, so the tabs would show the wrong thing. Retry to load them again.",
    retry: "Retry",
  },
  // The route-level error boundary fallback: a render error was caught instead of
  // blanking the page.
  crashed: {
    title: "This environment couldn’t be displayed",
    body: "Something went wrong rendering this page. Reload to try again, or go back to your environments.",
    reload: "Reload",
  },
  runBlocked: {
    agent: "No agent is connected to this environment yet",
    scenarios: "Add scenarios on the Scenarios tab",
    generic: "Setup incomplete",
    starting: "Starting a run",
    notReady: "This environment hasn't finished building",
    noRunTest: "This environment isn't linked to a run yet. Rebuild it to run simulations.",
    detailFailed: "This environment's details didn't load. Retry them first.",
    detailLoading: "Loading this environment's details",
  },
  // The env-version pin: lists every version of the world, and makes an
  // earlier one the default.
  pin: {
    heading: "Environment versions",
    subtitle:
      "Runs use the default version. You can roll back to an earlier one.",
    default: "default",
    rollBack: (label) => `Roll back to ${label}`,
  },
  // Banner shown while the derivation is still in flight — the panels below are
  // loading and this explains why.
  building: {
    title: "Environment is still being built",
    progress: (done, total) => `${done} of ${total} steps done.`,
    deriving: "Scenarios and personas are still deriving.",
    tail: "You can leave and come back. This page will fill in as each stage lands.",
  },
  // The gap-count badge on a tab: a blocking setup gap the reader still owns.
  // The tooltip heads the list of gap titles routed to that tab.
  gaps: {
    tooltip: "Needs your input before you can run:",
  },
  // The strip under the header: the environment / agent version pairing when
  // versioning is on, else the agent version under test; stated, not selected.
  versionBar: {
    envPrefix: "env",
    agentPrefix: "agent",
    suitePrefix: "scenarios",
    // Labels the agent in the versioning pairing (the environment's default).
    defaultAgent: "Default",
    // Labels the agent under test when versioning is off.
    testSubject: "Test subject",
    // The agent has no name on record; the Overview agent card says the same.
    unnamedAgent: "Agent",
    beforeVersioning: "before versioning",
    noAgent: "none",
    scenariosShared: (n) =>
      `${n} scenario${n === 1 ? "" : "s"}, shared across agent versions`,
    // Either label can be missing: an environment with agent versions but no
    // environment version yet, or one with no default agent version.
    pairingTooltip: (envLabel, envNote, agentLabel) =>
      [
        envLabel
          ? `Runs replay environment ${envLabel}${envNote ? ` (${envNote})` : ""}.`
          : "This environment has no environment version yet.",
        agentLabel
          ? `The environment's default agent version is ${agentLabel}; other agent versions can run against the same environment.`
          : "No default agent version is set for this environment.",
      ].join(" "),
    noVersionsTooltip:
      "This environment has no versions yet. One is recorded the next time it is built or saved.",
    subjectTooltip:
      "The agent version runs are scored against. Other agents can run against this environment too, and the environment stays the same.",
    scenariosTooltip:
      "Scenarios belong to the environment, so the same set runs against any agent version",
    defaultSuiteTooltip: (label) =>
      `The number of scenarios in the default scenario version, ${label}. The same set runs against any agent version.`,
  },
};

// The Versions tab: the agent, environment and scenario versions of this
// environment, what changed in each, and which scenarios a suite version holds.
export const VERSIONS_COPY = {
  title: "Versions",
  subtitle:
    "Every change to the agent, the environment or its scenarios is kept as a version. Runs replay the default environment and scenario versions.",
  pieces: {
    agent: "Agent",
    environment: "Environment",
    suite: "Scenarios",
  },
  noneInPiece: "No versions yet",
  empty: {
    title: "This environment has no versions yet",
    body: "A version is recorded the next time the environment is built or saved.",
  },
  loadError: "Couldn't load the versions. Try again in a moment.",
  disabled: "Versioning is switched off for this environment.",
  default: "default",
  unlabelled: "Unnumbered version",
  status: { draft: "draft", discarded: "discarded" },
  origin: {
    authoring: "Built",
    extend: "Extended",
    chat: "Saved from chat",
    manual: "Edited",
    backfill: "Recorded from before versioning",
    nudge: "Rebuilt",
    creation: "Created",
    push: "Pushed",
    upload: "Uploaded",
    prompt: "Prompt changed",
    agent_payload: "Settings changed",
    credentials: "Credentials changed",
    provider_sync: "Synced from provider",
  },
  by: (name) => `by ${name}`,
  // Making a version the default; the move itself rebuilds nothing.
  makeDefault: {
    action: "Make default",
    actionFor: (label) => `Make ${label} the default`,
    title: (label, piece) => `Make ${label} the default ${piece}?`,
    pieces: {
      agent: "agent version",
      environment: "environment",
      suite: "scenario version",
    },
    body: "New runs use it. If this combination hasn't run before, the next run prepares it first.",
    confirm: "Make default",
    pending: "Making default…",
    error: "Couldn't change the default. Try again in a moment.",
  },
  detail: {
    pick: "Pick a version to see what changed in it.",
    changes: (label) => `What changed in ${label}`,
    compareWith: "Compared with",
    previous: "Its previous version",
    first:
      "This is the first version, so there is nothing earlier to compare with.",
    noChange: "Nothing changed.",
    noRecord: "No change record for this version.",
    notComparable: "These two versions can't be compared.",
    loadError: "Couldn't load the changes.",
    scenarios: (label, n) => `${n} scenario${n === 1 ? "" : "s"} in ${label}`,
    membersError: "Couldn't load the scenarios in this version.",
  },
  diff: {
    tools: "Tools",
    rules: "Rules",
    useCases: "Use cases",
    fields: "Contract fields changed",
    modality: "Modality",
    subGoals: "Sub-goals",
    files: "World files",
    filesUnknown:
      "World files of the earlier version aren't known, so file changes aren't shown.",
    added: "Added",
    removed: "Removed",
    changed: "Changed",
    scenariosAdded: "Scenarios added",
    scenariosRemoved: "Scenarios removed",
    scenariosChanged: "Scenarios changed",
    source: "Source changed",
    prompt: "Prompt changed",
    variables: "Variables",
  },
  members: {
    key: "Key",
    name: "Name",
    useCase: "Use case",
  },
};

// The environment's agent versions and their credentials (Overview → Manage
// versions). Credential values are never shown; only their names are.
export const AGENT_VERSIONS_COPY = {
  title: "Agent versions",
  empty: "This environment's agent has no versions yet.",
  loadError: "Couldn't load the agent versions. Try again in a moment.",
  disabled: "Versioning is switched off for this environment.",
  preflight: {
    pending: "Checking…",
    failed: "Check failed",
    passed: "Check passed",
  },
  checks: {
    passed: "passed",
    failed: "failed",
    skipped: "skipped",
  },
  missing: (names) => `Missing: ${names.join(", ")}`,
  invalid: (names) => `Rejected by the provider: ${names.join(", ")}`,
  checkError: (code) => `The check couldn't finish (${code}).`,
  // Why a version whose check passed is not this environment's default.
  activation: {
    environment_busy: "Not made default: the environment was busy",
    rollback_unavailable:
      "Not made default: this combination hasn't been prepared yet",
    not_parent_default:
      "Not made default: the default changed while it was checked",
    default_moved_by_hand:
      "Not made default: the default was changed by hand while it was checked",
    superseded: "Not made default: a later change is already the default",
    discarded: "Not made default: it was discarded while it was checked",
    environment_gone: "Not made default: the environment was deleted",
    not_requested:
      "Not made default: its credentials were changed for another environment",
    not_bound: "Not made default: this environment can't use this version",
    rerun_only: "Made for a single rerun; not used as the default",
    failed: "Not made default: something went wrong; make it default by hand",
  },
  activationOther: (code) => `Not made default (${code})`,
  // Read aloud when a version's check starts or lands.
  checkStatus: (label, status) => `${label}: ${status}`,
  discard: {
    action: "Discard",
    actionFor: (label) => `Discard ${label}`,
    title: (label) => `Discard ${label}?`,
    body: "It stays in the history but can no longer be run or made the default.",
    confirm: "Discard",
    pending: "Discarding…",
    error: "Couldn't discard this version. Try again in a moment.",
  },
  retry: {
    action: "Check again",
    actionFor: (label) => `Check ${label} again`,
    pending: "Starting…",
    error: "Couldn't start the check again. Try again in a moment.",
  },
  credentials: {
    title: "Credentials",
    subtitle: (label) => `Variables the default version ${label} runs with.`,
    none: "No credentials are stored for the default version.",
    noDefault: "There's no default agent version yet.",
    requiredMissing: "required, not set",
    change: "Change values",
    checkRunning: "A credentials check is still running",
    newNameHint:
      "Adding variables may not be possible yet: this environment may have to be prepared again with them.",
    formHint:
      "Only the values you fill in change; every other variable keeps its value. Saving makes a new agent version, which is checked before it becomes the default.",
    name: "Name",
    value: "Value",
    addVariable: "Add variable",
    // Drops the row from this change only; the stored value stays.
    removeVariable: (name) => `Don't change ${name || "this variable"}`,
    invalidName:
      "Use letters, digits and underscores, not starting with a digit.",
    duplicateName: "This name is used twice.",
    fileLabel: "Google service-account JSON",
    chooseFile: "Upload JSON file",
    removeFile: "Remove file",
    uploading: "Uploading…",
    uploadError: "Couldn't upload the file.",
    nothingToSave: "Fill in at least one value or upload a file.",
    cancel: "Cancel",
    save: "Save values",
    saving: "Saving…",
    saved: (label) => `Saved as ${label}.`,
    errors: {
      credential_blob_too_large:
        "These values are too large to store. Remove some and try again.",
      agent_versions_unavailable:
        "This environment's agent can't take new credentials yet.",
      credential_file_invalid: "The credential file isn't valid.",
      secret_not_found: "The uploaded file has expired. Upload it again.",
      credential_missing: (error) =>
        `A stored value for ${error?.name || "a variable"} is missing; enter it again.`,
      version_not_runnable:
        "These variables can't be used yet: this environment would have to be prepared again with them, and that's turned off.",
    },
    error: "Couldn't save the values. Try again in a moment.",
  },
  // A new upload, prompt or settings for the agent, made in the environment.
  change: {
    watching: (repository, ref) =>
      `Watching ${repository} @ ${ref || "the default branch"}`,
    builtFrom: (repository, ref) =>
      `Built from ${repository} @ ${ref || "the default branch"}`,
    watchHint:
      "New commits become new agent versions automatically; they're checked before becoming the default.",
    privateNotWatched: "Private repositories aren't watched for new commits.",
    checkRunning: "Another change is still being checked",
    upload: "Upload new source",
    uploading: (progress) => `Uploading… ${progress}%`,
    creating: "Saving…",
    editPrompt: "Edit prompt",
    prompt: "Prompt",
    evalInstructions: "Instructions used for evaluation",
    evalInstructionsHint: (provider) =>
      `Your ${provider || "provider"} assistant keeps its own prompt; this only changes how runs are judged.`,
    count: (length, max) =>
      `${length.toLocaleString()} / ${max.toLocaleString()}`,
    phoneNumber: "Phone number",
    phoneHint: "In international format, like +14155550100.",
    savePrompt: "Save",
    cancel: "Cancel",
    created: (label) =>
      label
        ? `New version ${label} is being checked.`
        : "The new version is being checked.",
    errors: {
      version_not_runnable:
        "A changed agent can't run yet: preparing new combinations is turned off.",
      agent_unchanged: "Nothing changed.",
      agent_connected:
        "This agent is managed on the Agents page; change it there.",
      agent_change_unsupported: "This agent's source doesn't take that change.",
      archive_source_not_found: "The upload expired; upload the folder again.",
    },
    error: "Couldn't save the change. Try again in a moment.",
  },
  // The environment nudge for a version, the same one the chat card shows.
  nudge: {
    title: "This environment can be rebuilt with this version",
    files: (n) => `${n} file${n === 1 ? "" : "s"} changed`,
    variables: (n) => `${n} variable${n === 1 ? "" : "s"} changed`,
    prompt: "prompt changed",
    settings: "settings changed",
    skipped: "Update skipped",
  },
};

export const RUN_DIALOG_COPY = {
  title: "Run simulation",
  pieces: {
    agent: "Agent version",
    environment: "Environment version",
    suite: "Scenario version",
  },
  default: "default",
  none: "None yet",
  unlabelled: "Unnumbered version",
  loading: "Loading versions…",
  loadError: "Couldn't load the versions. The run uses the current defaults.",
  disabled: "Versioning is switched off, so the run uses the current versions.",
  checking: "Checking these versions…",
  planError: "Couldn't check these versions for warnings.",
  refused: "These versions can't run.",
  rerunFallback:
    "That run's versions can't run now; using the current defaults.",
  rerunAgentFallback:
    "That run's agent version can't run now; using the current default agent.",
  allScenarios: (n, suite) =>
    `Runs all ${n} scenario${n === 1 ? "" : "s"} in ${suite}.`,
  allCurrentScenarios: (suite) => `Runs all scenarios in ${suite}.`,
  emptySuite: (suite) => `${suite} has no scenarios to run.`,
  noneInSuite: (suite) => `None of the selected scenarios are in ${suite}.`,
  selectedScenarios: (n) => `Runs ${n} selected scenario${n === 1 ? "" : "s"}.`,
  membersLoading: "Loading the scenarios in this version…",
  membersError: "Couldn't load the scenarios in this version.",
  notInSuite: (keys, suite) =>
    `${keys.length} selected scenario${keys.length === 1 ? " isn't" : "s aren't"} in ${suite} and will be skipped: ${keys.join(", ")}`,
  trials: (n) => `${n} repeat${n === 1 ? "" : "s"} each.`,
  cancel: "Cancel",
  run: "Run",
  runAnyway: "Run anyway",
  starting: "Starting…",
};

// Unsaved changes (drafts) to the environment and its scenarios, made live
// together by one Save. Counts and names only.
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
export const DRAFTS_COPY = {
  title: "Unsaved changes",
  pieces: { environment: "environment", suite: "scenarios" },
  environmentChanges: (n) => plural(n, "change"),
  suiteChanges: ({ added = 0, removed = 0, changed = 0 } = {}) =>
    `${added} added, ${removed} removed, ${changed} changed`,
  lastEdited: (name, when) =>
    name ? `last edited by ${name}, ${when}` : `last edited ${when}`,
  viewChanges: "View changes",
  save: "Save",
  saving: "Saving…",
  discard: "Discard",
  discarding: "Discarding…",
  discardTitle: "Discard unsaved changes?",
  discardBody:
    "The unsaved changes are removed. The saved versions stay as they are.",
  stale: (piece, from, now) =>
    `These unsaved changes started from ${piece} ${from || "an earlier version"}; it is now ${now || "a newer version"}. Discard them, or ask chat to make them again on the current version.`,
  busy: "Chat is working; save when it finishes",
  saveError: "Couldn't save the changes. Try again in a moment.",
  discardError: "Couldn't discard the changes. Try again in a moment.",
  errors: {
    draft_changed:
      "Someone changed these edits a moment ago; review them and save again",
    chat_working:
      "Chat is still saving its last changes; try again in a moment",
    rebuild_running: "An update is running; try again when it finishes",
    environment_busy:
      "The environment is being built or updated; try again when it finishes",
    draft_not_found: "These changes were already saved or discarded.",
    idempotency_key_reused:
      "These changes moved while saving; press Save again.",
  },
  // The Scenarios tab and the run dialog.
  unsaved: "Unsaved",
  saveToRun: "Save to run",
  saveToRunSelected: "Save the unsaved changes to run these scenarios",
  addedToDraft: "Added to unsaved changes",
  notInRun:
    "Unsaved changes aren't part of this run. Save them first to include them.",
};

// Comparing two runs of the same environment, picked on the Runs tab, scenario
// by scenario, on pass rates over trials.
export const COMPARE_COPY = {
  heading: "Compare runs",
  subtitle: (margin) =>
    `Pass rate over trials, per scenario. Changes within ±${margin} points are not meaningful; evals are shown for context only.`,
  pick: "Select 2 finished runs to compare",
  picked: (count) =>
    count < 2
      ? `${count} of 2 selected`
      : "2 of 2 selected. Clear one to pick another",
  selectRun: (label) => `Select ${label}`,
  onlyFinished: "Only finished runs can be compared",
  atCap: "2 runs already selected",
  compare: "Compare",
  clear: "Clear",
  loading: "Comparing runs…",
  error: "Couldn't compare these runs. Close this and select two other runs.",
  versions: (label, pieces) => `${label}: ${pieces || "before versioning"}`,
  pieces: { agent: "Agent", environment: "Environment", suite: "Scenarios" },
  verdicts: {
    improved: "Improved",
    regressed: "Regressed",
    no_meaningful_change: "No meaningful change",
    inconclusive: "Inconclusive",
  },
  bands: {
    new: "New",
    not_applicable: "N/A",
    criteria_changed: "Criteria changed",
  },
  interval: (low, high) => `95% range ${low} to ${high} pts`,
  newHint: "Only in this run",
  newInComparedHint: "Only in the compared run",
  notApplicableHint: "Not in this run",
  notRunInComparedHint: "Not in the compared run",
  removed: (reason, label) => {
    const detail = (reason || "").replace(/^removed\s*/, "");
    if (label)
      return `Removed in Scenarios ${label}${detail ? ` (${detail})` : ""}`;
    return reason ? reason.charAt(0).toUpperCase() + reason.slice(1) : null;
  },
  criteriaHint: (names) => `Sub-goal changed: ${names.join(", ")}`,
  columns: {
    scenario: "Scenario",
    current: "This run",
    against: "Compared run",
    change: "Change",
    verdict: "Verdict",
    evals: "Evals",
  },
  passed: (passed, trials, undecided) =>
    `${passed} / ${trials} trials${undecided ? ` · ${undecided} undecided` : ""}`,
  summary: {
    improved: "improved",
    regressed: "regressed",
    no_meaningful_change: "no meaningful change",
    inconclusive: "inconclusive",
    new: "new",
    not_applicable: "n/a",
    criteria_changed: "criteria changed",
  },
};

// The run's agent is not the one its environment version was built with.
const shortVersion = (prefix, label) => {
  const number = /^v(\d+)$/i.exec(label || "")?.[1];
  if (number) return `${prefix}${number}`;
  return label ? `${prefix} ${label}` : null;
};
export const agentDiffersTag = (agentLabel, envLabel) => {
  const agent = shortVersion("A", agentLabel);
  const env = shortVersion("E", envLabel);
  return agent && env
    ? `${agent} differs from ${env}`
    : "Agent differs from its environment version";
};
