// Copy and request bodies for the Add scenarios drawer. Every route ends with the builder writing
// drafts into the suite draft, each proved as it is written, so each new case starts in Draft and
// shows its assumptions as sources.

export const ADD_COPY = {
  title: "Add scenarios",
  subtitle:
    "Your agent's scenarios are already here. These routes ask the builder to write more; each new case starts as Not proved.",
  writing:
    "The builder is writing these cases. They appear as Not proved on the Scenarios tab.",
  cancel: "Cancel",
  submit: "Write scenarios",
  count: "How many",
  guidance: "Guidance (optional)",
  describe: "What should the agent face, and what should it do?",
  file: "Choose a file",
  fileHint: "Text or Markdown, at most 1 MB.",
  recordsHint: "CSV or JSON rows, at most 1 MB.",
  tooLarge: "The file is larger than 1 MB.",
  transcriptLink: "Add a transcript on the Scenarios tab",
  actors: "Actors to include",
  groundIn: "Ground in",
  ruleOption: (text) => `Rule: ${text}`,
  fileOption: (path) => `File: ${path}`,
  productionLink: "Open Observe and use Create environment test",
  routes: {
    generate: {
      label: "Generate more",
      blurb: "More cases from this environment's rules, tools and data.",
    },
    describe: {
      label: "Describe a case",
      blurb:
        "Say what the agent should face; the builder writes and proves it.",
    },
    file: {
      label: "From a policy file",
      blurb: "Upload a policy; each requirement is cited with its exact words.",
    },
    records: {
      label: "From records (CSV/JSON)",
      blurb: "Upload rows; each case cites the row it came from.",
    },
    elsewhere: {
      label: "From a transcript or production",
      blurb: "Start from a real conversation.",
    },
  },
};

export const ADD_ROUTES = [
  "generate",
  "describe",
  "file",
  "records",
  "elsewhere",
];
export const MAX_COUNT = 20;
export const UPLOAD_MAX_BYTES = 1024 * 1024;
export const POLICY_TYPES = ".md,.txt";
export const RECORD_TYPES = ".csv,.json";

export const DESCRIBE_PREFIX =
  "Add one scenario for each requested case below. Cite each as a requirement source whose quote is that request's line exactly, and add a negative control to each:";

// Each non-empty line of a description is its own numbered request, so a case the builder has
// not written yet can be found by its line.
export const requestedLines = (description) =>
  String(description || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
export const describeMessage = (description) =>
  `${DESCRIBE_PREFIX}\n${requestedLines(description)
    .map((line, at) => `Requested case ${at + 1}: ${line}`)
    .join("\n")}`;

// The actors and sources a person picked, said to the builder; nothing when none were picked.
export const groundingSuffix = (actors = [], rules = [], files = []) => {
  const parts = [];
  if (actors.length) parts.push(` Include these actors: ${actors.join(", ")}.`);
  const cited = [
    ...rules.map((rule) => `rule "${rule}"`),
    ...files.map((path) => `file ${path}`),
  ];
  if (cited.length) parts.push(` Cite these sources: ${cited.join("; ")}.`);
  return parts.join("");
};

// Until the builder reads uploaded files itself, the message carries the file's text.
// "Add ... new" and "keep the existing ones": asked only to "write" scenarios, the builder answered by
// describing the cases it already had.
export const fileMessage = (count, path, text) =>
  `Add ${count} new ${count === 1 ? "scenario" : "scenarios"} to the suite, grounded in this file (${path}); keep the existing ones as they are. Cite each requirement as a file source with the exact quote, and add a negative control to each:\n\n${text}`;

export const recordsMessage = (count, path, text) =>
  `Add ${count} new ${count === 1 ? "scenario" : "scenarios"} to the suite, one per row in ${path}; keep the existing ones as they are. Cite each as a file source quoting its row, and add a negative control to each:\n\n${text}`;

export const missingCasesGuidance = (uncovered) =>
  `Write scenarios for these planned levels that no scenario covers yet: ${uncovered
    .map((one) => `${one.axis}: ${one.level}`)
    .join("; ")}`;

export const clampCount = (value) =>
  Math.max(1, Math.min(MAX_COUNT, Number(value) || 1));
