// Copy and request bodies for the Add scenarios drawer. Every route ends with the builder writing
// drafts into the suite draft, each proved as it is written, so each new case starts in Draft and
// shows its assumptions as sources.

export const ADD_COPY = {
  title: "Add scenarios",
  subtitle:
    "Your agent's scenarios are already here. These routes ask the builder to write more; each new case starts as Draft.",
  writing: "The builder is writing these cases. They appear as Draft on the Scenarios tab.",
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
  productionLink: "Open Observe and use Create environment test",
  routes: {
    generate: { label: "Generate more", blurb: "More cases from this environment's rules, tools and data." },
    describe: { label: "Describe a case", blurb: "Say what the agent should face; the builder writes and proves it." },
    file: { label: "From a policy file", blurb: "Upload a policy; each requirement is cited with its exact words." },
    records: { label: "From records (CSV/JSON)", blurb: "Upload rows; each case cites the row it came from." },
    elsewhere: { label: "From a transcript or production", blurb: "Start from a real conversation." },
  },
};

export const ADD_ROUTES = ["generate", "describe", "file", "records", "elsewhere"];
export const MAX_COUNT = 20;
export const UPLOAD_MAX_BYTES = 1024 * 1024;
export const POLICY_TYPES = ".md,.txt";
export const RECORD_TYPES = ".csv,.json";

export const DESCRIBE_PREFIX =
  "Add one scenario from this description, cite it as a requirement in the person's words, and add a negative control:";

// Until the builder reads uploaded files itself, the message carries the file's text.
export const fileMessage = (count, path, text) =>
  `Write ${count} scenarios grounded in this file (${path}); cite each requirement as a file source with the exact quote:\n\n${text}`;

export const recordsMessage = (count, path, text) =>
  `Write ${count} scenarios from the rows in ${path}; cite each as a file source quoting the row.\n\n${text}`;

export const missingCasesGuidance = (uncovered) =>
  `Write scenarios for these planned levels that no scenario covers yet: ${uncovered
    .map((one) => `${one.axis}: ${one.level}`)
    .join("; ")}`;

export const clampCount = (value) => Math.max(1, Math.min(MAX_COUNT, Number(value) || 1));
