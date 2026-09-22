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
  { id: "contract", label: "Contract", icon: "solar:document-text-linear" },
  { id: "scenarios", label: "Scenarios", icon: "solar:layers-minimalistic-linear", badge: "scenarios" },
  { id: "evals", label: "Evaluations", icon: "solar:shield-check-linear", badge: "evals" },
  { id: "runs", label: "Runs", icon: "solar:play-circle-linear", badge: "runs" },
  { id: "settings", label: "Settings", icon: "solar:settings-linear" },
];

// Suggested builder prompts per tab — the same console the build screen uses.
// The `build` key feeds the build page's in-place workspace before 7/7.
export const CHIPS_BY_TAB = {
  overview: [
    "Summarise what's in this environment",
    "What's still missing before we can run?",
    "Explain the tools and rules to me",
  ],
  contract: [
    "Tighten the refund rule",
    "Add a hard rule about escalations",
    "Explain the reward function",
  ],
  scenarios: [
    "Add a dispute case",
    "Add an edge case where a tool fails",
    "Rewrite the rushed-caller persona",
  ],
  evals: [
    "Add a grader for tool-choice correctness",
    "Tighten the hand-off quality bar",
    "Explain what each grader measures",
  ],
  build: [
    "Why was this tool included?",
    "What did we infer vs read directly?",
  ],
  runs: [
    "Summarise the last run",
    "Which scenarios fail most often?",
  ],
  settings: [
    "Rotate my OpenAI key",
    "Which env vars are the grader reading?",
    "Change the task timeout to 10 minutes",
  ],
};

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
  live: "Live",
  buildingLabel: "Building",
  failedLabel: "Failed",
  liveTooltip: "Environment is live. You can edit via the builder or inline.",
  buildingTooltip: "Environment is still being built…",
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
  // The route-level error boundary fallback: a render error was caught instead of
  // blanking the page.
  crashed: {
    title: "This environment couldn’t be displayed",
    body: "Something went wrong rendering this page. Reload to try again, or go back to your environments.",
    reload: "Reload",
  },
  runBlocked: {
    agent: "Connect an agent on the Agents tab",
    scenarios: "Add scenarios on the Scenarios tab",
    evals: "Add at least one evaluation on the Evaluations tab",
    generic: "Setup incomplete",
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
    tail: "You can leave and come back — this page will fill in as each stage lands.",
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
  // The pairing strip under the header: a run is this environment version
  // against that agent version, stated rather than selected.
  versionBar: {
    envPrefix: "env",
    agentPrefix: "agent",
    testSubject: "Test subject",
    scenariosShared: (n) => `${n} scenarios, shared across agent versions`,
    pairingTooltip: (envLabel, envNote, agentLabel) =>
      `Environment ${envLabel} — ${envNote}. Test subject: agent ${agentLabel}. Different agents can run against this env; the env stays put.`,
    scenariosTooltip:
      "Scenarios belong to the environment, so the same set runs against any agent version",
  },
};
