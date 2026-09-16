// Build-stage copy and the client build stage enum. Strings ported verbatim
// from the designer's BuildFromAgent.jsx (header + pipeline popover),
// DerivedPanels.jsx (deriving labels + tab rail) and AssistantConsole.jsx.

// BUILD_STAGE lives in the store (single source shared with the build slice);
// re-exported here so the build modules keep importing it from this file.
export { BUILD_STAGE } from "../store/useEnvironmentsStore";

// The DerivingAnimation copy, one line per milestone the builder is working on,
// plus the loading branch once all three are done. Verbatim from DerivedPanels.
export const DERIVING_LABEL = {
  understand: "Reading your agent — extracting tools and rules",
  build: "Building the world — seeding data and wiring handlers",
  scenarios: "Writing scenarios — proving each one solvable",
  loading: "Loading the editor for what we derived",
  idle: "Reading your agent…",
};

// The muted tab rail on the building pane (labels only in Phase-2).
export const BUILDING_TABS = [
  { id: "overview", label: "Overview" },
  { id: "contract", label: "Contract" },
  { id: "scenarios", label: "Scenarios" },
  { id: "evals", label: "Evaluations" },
];

export const BUILD_HEADER_COPY = {
  back: "Change source",
  rename: "Rename",
  setupBuilding: "Setup being built",
  ready: "Ready to run",
  failedAt: (label) => `Failed at ${label.toLowerCase()}`,
  run: "Run simulation",
  runBlocked: "Finish the three stages on the left first",
  popoverTitle: "Build pipeline",
  halted: "halted",
  running: "running",
  readyShort: "ready",
  paused: "paused",
};

export const CONSOLE_COPY = {
  working: "Working on your last message…",
  idle: "Ask, correct, or steer what it builds",
  empty: "Answer the questions below to kick things off. You can also just start typing.",
  placeholder: "Reply to the builder…",
  attach: "Attach a dataset, CSV or file",
  attachAccept: ".csv,.tsv,.json,.jsonl,.xlsx,.txt,.md,.pdf",
  workingDot: "Working…",
};

export const PIPELINE_CHECKS_COPY = {
  heading: "Setup — building the environment",
  queued: "queued",
};
