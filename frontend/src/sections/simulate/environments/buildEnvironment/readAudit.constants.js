// Read-audit (the designer's AgentReadReceipt) constants: the four dimensions we
// read from an agent, the reader's health states, the demo section gaps, and all
// the copy. Strings ported verbatim from the designer's AgentReadReceipt.jsx.

export const READER_STATUS = {
  HEALTHY: "healthy",
  WARNING: "warning",
  HARDFAIL: "hardfail",
};

// Query param that force-shows a reader health state (?readerStatus=warning).
export const READER_STATUS_PARAM = "readerStatus";

export const READ_SECTIONS = [
  { key: "tools", title: "Tools it can call", subtitle: "From the agent's config and call-graph", icon: "solar:code-square-linear" },
  { key: "rules", title: "Rules it must hold", subtitle: "Data from policy.yaml — not judgement", icon: "solar:shield-check-linear" },
  { key: "data", title: "Data it starts with", subtitle: "Fixtures that seed the world every run", icon: "solar:database-linear" },
  { key: "behavior", title: "How it behaves", subtitle: "Prompt, voice, routing", icon: "solar:playlist-linear" },
];

export const READ_SECTION_KEY = {
  TOOLS: "tools",
  RULES: "rules",
  DATA: "data",
  BEHAVIOR: "behavior",
};

// Baseline warnings used when the reader completed with gaps and the payload
// didn't supply its own. Each entry says WHY the section is empty and, where
// sensible, offers a per-section retry. Verbatim from DEFAULT_DEMO_ISSUES.
export const MOCK_SECTION_ISSUES = {
  rules: {
    severity: "warning",
    icon: "solar:file-remove-linear",
    message: "No policy.yaml found in the repo",
    hint: "Rules can still be captured — add them manually or point us at a different branch.",
    retryLabel: "Retry read",
  },
  data: {
    severity: "warning",
    icon: "solar:clock-circle-linear",
    message: "Call-graph timed out at 30s — 2 fixtures not resolved",
    hint: "The scan is deterministic; retrying often succeeds once other jobs free the workers.",
    retryLabel: "Retry read",
  },
};

export const READ_AUDIT_COPY = {
  title: "What we read from your agent",
  subtitle:
    "Every fact below is tagged by how we know it — resolve the open questions on the right before we build.",
  empty: "Nothing read from this dimension.",
  hardfailDefault: "No response from the agent source. The reader gave up after 30s.",
  hardfailTitle: "We couldn’t read your agent",
  retry: "Retry read",
  changeSource: "Change source",
  continueDefaults: "Continue without reading — build with template defaults",
  checksTitle: "Preflight checks that did not pass",
  checksSubtitle: "Reported by the preflight itself — clear these before the build can run.",
  checkMissing: "Missing",
  checkFix: "Fix",
  questionsTitle: "Open questions",
  allAnswered: "All answered — the world will be built on named facts",
  build: "Build the environment",
  skippedNote: "Skipped — the affected fact will carry an INFERRED marker.",
  other: "Other",
  otherPlaceholder: "Type your own answer here",
  back: "Back",
  skip: "Skip",
  next: "Next",
};

// Subtitle for the open-questions column — "all answered" once everything is
// resolved, otherwise a running "N of total still open" count.
export const openQuestionsSubtitle = (open, total) =>
  open === 0
    ? READ_AUDIT_COPY.allAnswered
    : `${open} of ${total} still open — resolve them before we build against them`;

// Tooltip on the disabled "Build the environment" button while questions remain.
export const blockedHint = (open) =>
  `${open} open ${open === 1 ? "question" : "questions"} still block the build`;

// Note shown once questions have been skipped rather than answered.
export const skippedHint = (n) =>
  `${n} skipped — the affected facts will carry an INFERRED marker in the built world`;

// Section gap shown when a real preflight was skipped (e.g. no uploaded archive).
export const PREFLIGHT_SKIPPED_ISSUE = (reason) => ({
  severity: "warning",
  icon: "solar:link-broken-minimalistic-linear",
  message: "Preflight skipped",
  hint: reason,
  retryLabel: null,
});
