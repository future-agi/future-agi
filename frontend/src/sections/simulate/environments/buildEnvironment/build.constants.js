// Build-stage copy for the deriving hero, the building tab rail and the builder
// console. Strings ported verbatim from the designer's DerivedPanels.jsx
// (deriving labels + tab rail) and AssistantConsole.jsx.

// The DerivingAnimation copy, one line per milestone the builder is working on,
// plus the loading branch once all three are done. Verbatim from DerivedPanels.
export const DERIVING_LABEL = {
  understand: "Reading your agent — extracting tools and rules",
  build: "Building the world — seeding data and wiring handlers",
  scenarios: "Writing scenarios — proving each one solvable",
  loading: "Loading the editor for what we derived",
  idle: "Reading your agent…",
  // Preflight already passed inline; this stage is the create call landing.
  creating: "Creating your environment…",
  // Terminal-failed build: the hero reads as failed (the specific reason shows
  // on the failed pipeline step below), not the raw stage name.
  failed: "Build failed — the environment couldn’t be assembled",
  // Fallback for the source-panel header before a source label is known.
  readingSource: "reading source…",
};

// The tab rail on the building pane. It re-exports the workspace tabs so the
// muted loading rail and the live workspace rail always show the same five
// labels (including Runs); the building pane just renders them pointer-dead.
export { WORKSPACE_TABS as BUILDING_TABS } from "../workspace/workspace.constants";

export const CONSOLE_COPY = {
  working: "Working on your last message…",
  idle: "Ask, correct, or steer what it builds",
  empty: "Answer the questions below to kick things off. You can also just start typing.",
  placeholder: "Reply to the builder…",
  attach: "Attach a dataset, CSV or file",
  attachAccept: ".csv,.tsv,.json,.jsonl,.xlsx,.txt,.md,.pdf",
  workingDot: "Working…",
  frozen: "Environment is still being built",
  mode: "Builder mode — Auto or Manual",
};

export const PIPELINE_CHECKS_COPY = {
  heading: "Setup — building the environment",
  queued: "queued",
};
