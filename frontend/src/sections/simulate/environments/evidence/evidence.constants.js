import {
  MAX_EVIDENCE_SOURCES,
  MAX_TRANSCRIPT_CHARS,
} from "src/api/simulate-environments/evidenceImports";

// What the import review says. One term per thing: an "import" holds "cases",
// each from one Observe session or call, one dataset row, or one transcript a
// person added; the
// builder "drafts" a scenario for a case; a person "adds" drafted cases to the
// suite.

export const IMPORT_PARAM = "import";

export const EVIDENCE_ACTION_ID = "create-environment-test";

export const EVIDENCE_COPY = {
  action: "Create environment test",
  actionTooMany: `Pick at most ${MAX_EVIDENCE_SOURCES} to import at once`,
  actionAll: "Pick the items to import; Select all is not supported here",
  actionNoProject: "Open a project to import its evidence",
  dialogTitle: "Create environment test",
  dialogBody: (count, kind) =>
    `${count} ${kind}${count === 1 ? "" : "s"} from this project become a draft import. Nothing runs, and nothing joins the suite until you add it.`,
  datasetDialogBody: (count) =>
    `${count} row${count === 1 ? "" : "s"} from this dataset become a draft import. The rows are read again each time you review them; nothing of them is copied. Nothing runs, and nothing joins the suite until you add it.`,
  conversationColumn: "Conversation column",
  conversationHelp:
    'Chat messages as JSON, or lines that start "User:" or "Agent:". Audio-only rows are not supported yet.',
  labelColumns: "Label columns (optional)",
  expectedColumn: "Expected outcome column (optional)",
  expectedHelp:
    "What should happen. The scenario's expected behaviour is written from it; where it contradicts the agent's policy, the policy wins.",
  noColumn: "None",
  columnsLoading: "Loading this dataset's columns…",
  noTextColumn:
    "This dataset has no text or JSON column to read a conversation from. Audio-only rows are not supported yet.",
  rowsProduction: "These rows come from production",
  rowsProductionHelp:
    "Leave it off for rows you wrote or ones from testing: their scenarios then never claim a customer did this.",
  environmentLabel: "Environment",
  noEnvironments: "No environment to import into yet. Create one first.",
  environmentsLoading: "Loading your environments…",
  environmentsError:
    "Couldn't load your environments. Close this and try again in a moment.",
  create: "Import",
  creating: "Importing…",
  importButton: "Import evidence",
  importHintTitle: "Import evidence",
  importHint:
    "Pick sessions or calls in Observe, then choose Create environment test. Or add a transcript you have: paste it or upload files. The import opens here for review.",
  openObserve: "Open Observe",
  addTranscript: "Add a transcript",
  transcriptTitle: "Add a transcript",
  transcriptBody:
    'Paste a transcript or upload files: JSON messages, lines that start "User:" or "Agent:", or CSV with a role and a text column. Personal data is replaced with tokens before anything is stored, and only what fits the import\'s size is kept.',
  pasteLabel: "Transcript",
  nameLabel: "Name",
  pastedName: "Pasted transcript",
  formatLabel: "Format",
  formats: {
    json: "JSON messages",
    text: "User: / Agent: lines",
    csv: "CSV with role and text",
  },
  uploadFiles: "Upload files",
  usePaste: "Paste instead",
  production: "These transcripts come from production",
  productionHelp:
    "Leave it off for a transcript you wrote or one from testing: its scenarios then never claim a customer did this.",
  transcriptTooLong: (name) =>
    `${name} is longer than ${MAX_TRANSCRIPT_CHARS.toLocaleString("en-US")} characters.`,
  transcriptsTooMany: `Pick at most ${MAX_EVIDENCE_SOURCES} files.`,
  title: "Review imported evidence",
  subtitle: (count, kind) => {
    const plural = count === 1 ? "" : "s";
    if (kind === "transcript") return `${count} transcript${plural} you added`;
    if (kind === "dataset") return `${count} row${plural} from a dataset`;
    return `${count} ${kind}${plural} from Observe`;
  },
  // A transcript is the person's, never a recording Observe made.
  yourTranscript: (production) =>
    production ? "Your transcript, from production" : "Your transcript",
  // A dataset row is the person's too; its provenance is what they said.
  yourRow: (production) =>
    production ? "Dataset row, from production" : "Dataset row",
  datasetNotice: (production) =>
    production
      ? "You said these rows come from production, so each case is regression evidence. They are read from the dataset again each time, not recordings Observe made."
      : "You did not mark these rows as production: their scenarios are tests, and never claim a customer did this.",
  transcriptNotice: (production) =>
    production
      ? "You said these transcripts come from production, so each case is regression evidence. They are your transcripts, not recordings Observe made."
      : "You did not mark these transcripts as production: their scenarios are tests, and never claim a customer did this.",
  // A Simulator project's sources are our own simulations, never production evidence.
  simulated: (kind) => `Simulated ${kind}, not production`,
  simulatedNotice: (kind) =>
    `These ${kind}s come from a Simulator project: they are simulations, not production. The scenarios drafted from them never claim a customer did this.`,
  notFound: "This import is not available.",
  loadError: "Couldn't read this import from Observe. Try again in a moment.",
  stepEvidence: "1. Evidence",
  stepState: "2. Starting state",
  stepDraft: "3. Drafted scenarios",
  stepAdd: "4. Add to the suite",
  redactionReady: (total) =>
    total
      ? `${total} value${total === 1 ? "" : "s"} of personal data will be replaced with tokens before anything becomes scenario text.`
      : "No personal data was found.",
  redactionStored: (total) =>
    total
      ? `${total} value${total === 1 ? "" : "s"} of personal data ${total === 1 ? "was" : "were"} replaced with tokens when the transcripts were stored.`
      : "No personal data was found when the transcripts were stored.",
  shows: "What should the new scenarios be?",
  showsOptions: {
    failure: "Reproduce the failure as a regression test",
    success: "Keep this success as a regression test",
    partial: "A regression test from partial evidence",
  },
  // Evidence that is not production (a Simulator project's, or transcripts
  // nobody marked as production) makes tests, never regression tests.
  showsOptionsNotProduction: {
    failure: "Reproduce the failure as a test",
    success: "Keep this success as a test",
    partial: "A test from partial evidence",
  },
  // What each choice keeps from the evidence, and what the builder makes up.
  showsDetail: {
    failure:
      "Keeps: the situation, what the user said and the failure evidence. Makes: what should have happened, written from the policy, never copied from the agent's answer.",
    success:
      "Keeps: the workflow that worked, step by step. Makes: explicit checks; an observed success still needs a reviewed expected outcome.",
    partial:
      "Keeps: only what the record does show. Makes: checks for that part; anything it does not show is labelled synthetic.",
  },
  startingState:
    "Observe never records the records the agent's tools read before the conversation (an earlier refund, an order's age). The draft seeds what the evidence shows and labels everything it assumes as synthetic. If you have the real record, add it to the scenario after it is drafted: the test is not an exact replay.",
  startingStateDataset:
    "A dataset row never records the records the agent's tools read before the conversation (an earlier refund, an order's age). The draft seeds what the row shows and labels everything it assumes as synthetic. If you have the real record, add it to the scenario after it is drafted: the test is not an exact replay.",
  startingStateTranscript:
    "A transcript never records the records the agent's tools read before the conversation (an earlier refund, an order's age). The draft seeds what the transcript shows and labels everything it assumes as synthetic. If you have the real record, add it to the scenario after it is drafted: the test is not an exact replay.",
  draft: (n) => `Draft ${n} scenario${n === 1 ? "" : "s"}`,
  drafting: (n) => `Drafting ${n} imported case${n === 1 ? "" : "s"}`,
  draftingBody:
    "The environment's builder is writing a scenario for each case. Changing the environment meanwhile can block the drafted cases.",
  noneWaiting: "No case is waiting to be drafted.",
  drafted: "Drafted",
  blocked: "Blocked",
  added: "Added to the suite",
  unavailable: "Unavailable",
  pending: "Waiting",
  newCheck: "New check added by this import",
  scenarioKey: "Scenario",
  provenanceUnavailable:
    "Provenance unavailable: Observe no longer keeps this source.",
  provenanceUnavailableRow:
    "Provenance unavailable: the dataset no longer holds this row.",
  expectedOutcome: "Expected outcome",
  changed: "The source changed since it was imported.",
  addClean: (n) => `Save ${n} to the suite`,
  addBeside: (n) => `Add ${n} to unsaved changes`,
  besideNotice:
    "The environment has unsaved changes. These cases join them and stay unsaved until you press Save in the draft bar. Until then, Fix my agent cannot write new variations.",
  besideAdded: (n) =>
    `${n} case${n === 1 ? " is" : "s are"} in the unsaved changes. Press Save in the draft bar to make ${n === 1 ? "it" : "them"} live; until then, Fix my agent cannot write new variations.`,
  goToSave: "Go to Save",
  added_: (n) => `${n} case${n === 1 ? "" : "s"} added to the suite`,
  addedToDraft: (n) =>
    `${n} case${n === 1 ? "" : "s"} added to the unsaved changes`,
  errors: {
    evidence_drafting_busy: "These cases are being drafted already.",
    evidence_case_not_pending: "Only waiting cases can be drafted.",
    evidence_case_not_drafted: "Only drafted cases can be added.",
    evidence_draft_stale:
      "The environment changed since these cases were drafted. Import them again to draft them on the current environment.",
    draft_changed:
      "The unsaved changes moved since this review opened. Look at them and add again.",
    drafts_unavailable:
      "This environment keeps no unsaved changes, so imported cases cannot be added.",
    draft_not_runnable:
      "These changes can't be made live yet: the environment hasn't been prepared for them.",
    chat_working:
      "Chat is still working on this environment; add when it finishes.",
  },
  genericError: "Something went wrong. Try again in a moment.",
};

export const PART_LABELS = {
  transcript: "Transcript",
  tool_calls: "Tool calls",
  tool_inputs: "Tool inputs",
  tool_outputs: "Tool outputs",
  labels: "Labels",
  starting_state: "Starting state",
  expected_outcome: "Expected outcome",
};

// A part's status as a StatusBadge role.
export const PART_ROLE = {
  present: "success",
  partial: "warning",
  truncated: "warning",
  missing: "neutral",
  unreadable: "danger",
};

// A case's status as a StatusBadge role; a blocked case never reads as passed.
export const CASE_ROLE = {
  pending: "neutral",
  drafted: "info",
  added: "success",
  blocked: "danger",
  unavailable: "neutral",
};

// A transcript's format from its file's extension, else from how it starts (a
// byte-order mark is whitespace to trimStart). The person can change it; the
// backend names the line it cannot read.
export const guessFormat = (name, text) => {
  const extension = /\.([a-z]+)$/i.exec(name ?? "")?.[1]?.toLowerCase();
  if (extension === "json") return "json";
  if (extension === "csv") return "csv";
  if (extension === "txt") return "text";
  const start = (text ?? "").trimStart();
  if (start.startsWith("[") || start.startsWith("{")) return "json";
  const header = start.split(/\r?\n/, 1)[0].toLowerCase();
  if (header.includes(",") && /\b(role|speaker)\b/.test(header)) return "csv";
  return "text";
};

export const errorText = (error) =>
  EVIDENCE_COPY.errors[error?.error] ||
  error?.message ||
  EVIDENCE_COPY.genericError;
