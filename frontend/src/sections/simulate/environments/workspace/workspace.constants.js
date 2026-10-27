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
  liveTooltip: "Environment is ready to run. You can edit via the builder or inline.",
  buildingTooltip: "Environment is still being built…",
  finalizingTooltip: "Wrapping up the build…",
  cancellingLabel: "Cancelling",
  cancellingTooltip: "Stopping the build and cleaning up its sandbox.",
  cancelledLabel: "Cancelled",
  cancelledTooltip: "You cancelled this build.",
  runningTooltip: "A simulation is running. Results land under Runs when it finishes.",
  failedTooltip: "This environment failed to build.",
  run: "Run simulation",
  moreActions: "More actions",
  fork: "Fork environment",
  forkHint: "Duplicate the world for a different agent or team. Agent + runs reset.",
  deleteEnv: "Delete environment",
  deleteEnvHint: "Remove this environment and cancel any live run. This can't be undone.",
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
  // The env-version pin: the menu that lists every version of the world so the
  // reader can switch which one the next run stamps against.
  pin: {
    heading: "Environment versions",
    subtitle: "A run pins whichever version is active when it starts.",
    latest: "latest",
    active: "active",
    scenarios: (n) => `${n} scenarios`,
  },
  // Banner shown while the derivation is still in flight — the panels below are
  // loading and this explains why.
  building: {
    title: "Environment is still being built",
    progress: (done, total) => `${done} of ${total} steps done.`,
    deriving: "Scenarios and personas are still deriving.",
    tail: "You can leave and come back. This page will fill in as each stage lands.",
  },
  // Banner shown when an older env version is pinned — the amber pin is the
  // control, this is the reminder so edits do not silently sit on the wrong world.
  offLatest: {
    title: (label) => `Editing off ${label}`,
    body: (active, newest) =>
      `Latest is ${newest}. Runs from here will stamp against ${active}, and any edits sit on ${active}, not on the latest world.`,
    action: (label) => `Switch to ${label}`,
  },
  // The gap-count badge on a tab: a blocking setup gap the reader still owns.
  // The tooltip heads the list of gap titles routed to that tab.
  gaps: {
    tooltip: "Needs your input before you can run:",
  },
  // The strip under the header: the agent version under test, stated rather
  // than selected.
  versionBar: {
    envPrefix: "env",
    testSubject: "Test subject",
    // The agent has no name on record; the Overview agent card says the same.
    unnamedAgent: "Agent",
    scenariosShared: (n) => `${n} scenarios, shared across agent versions`,
    subjectTooltip:
      "The agent version runs are scored against. Other agents can run against this environment too, and the environment stays the same.",
    scenariosTooltip:
      "Scenarios belong to the environment, so the same set runs against any agent version",
  },
};
