import {
  MAX_EVIDENCE_SOURCES,
  MAX_IMPORT_KEPT_CHARS,
  MAX_LABEL_COLUMNS,
  MAX_TRANSCRIPT_CHARS,
} from "src/api/simulate-environments/evidenceImports";

// What the import review says. One term per thing: an "import" holds "cases",
// each from one Observe session or call, one dataset row, or one transcript a
// person added; the
// builder "drafts" a scenario for a case; a person "adds" drafted cases to the
// suite.

export const IMPORT_PARAM = "import";

export const EVIDENCE_ACTION_ID = "create-environment-test";

// The kinds Observe keeps; a transcript or a dataset row comes from outside it.
export const OBSERVE_KINDS = new Set(["session", "call"]);

// A transcript or dataset import nobody marked as production: the mark is
// fixed at import, so changing it means importing again.
const NOT_PRODUCTION_NOTICE =
  "Not marked as production, so these become tests, not regression tests. To change it, discard this and import again.";

export const EVIDENCE_COPY = {
  action: "Create environment test",
  actionTooMany: `Pick at most ${MAX_EVIDENCE_SOURCES} to import at once`,
  actionAll: "Tick them one by one; Select all isn't available here",
  actionNoProject: "Open a project to import its evidence",
  dialogTitle: "Create environment test",
  dialogBody: (count, kind) =>
    `${count} ${kind}${count === 1 ? "" : "s"} from this project become a draft import. Nothing runs, and nothing joins the suite until you add it.`,
  datasetDialogBody: (count) =>
    `${count} row${count === 1 ? "" : "s"} from this dataset become a draft import, read again at each review. Nothing joins the suite until you add it.`,
  conversationColumn: "Conversation column",
  conversationHelp:
    'Chat messages as JSON, or lines that start "User:" or "Agent:". Audio-only rows are not supported yet.',
  labelColumns: "Label columns (optional)",
  labelColumnsHelp: `Read as context for the builder, such as intent. Up to ${MAX_LABEL_COLUMNS}.`,
  expectedColumn: "Expected outcome column (optional)",
  expectedHelp:
    "What should happen. The scenario's expected behaviour is written from it; the environment's rules win a conflict.",
  noColumn: "None",
  columnsLoading: "Loading this dataset's columns…",
  columnsError: "Couldn't load this dataset's columns.",
  noTextColumn:
    "This dataset has no text or JSON column to read a conversation from. Audio-only rows are not supported yet.",
  rowsProduction: "These rows come from production",
  rowsProductionHelp:
    "Ticked, they make regression tests; leave it off for rows you wrote or tested with. It can't change after import.",
  environmentLabel: "Environment",
  noEnvironments: "No environment to import into yet. Create one first.",
  environmentsLoading: "Loading your environments…",
  environmentsError: "Couldn't load your environments.",
  // The action and the button that confirms it share one name.
  create: "Create environment test",
  creating: "Creating…",
  importButton: "Import evidence",
  importHintTitle: "Import evidence",
  importHint:
    "Pick sessions or calls in Observe, or rows in a dataset, then choose Create environment test. Or add a transcript you have.",
  openObserve: "Open Observe",
  openDataset: "Open a dataset",
  addTranscript: "Add a transcript",
  transcriptTitle: "Add a transcript",
  transcriptBody: `Paste a transcript or upload files. Emails, phone, card and ID numbers become placeholders; an import keeps ${MAX_IMPORT_KEPT_CHARS.toLocaleString("en-US")} characters, shared by its files.`,
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
    "Ticked, they make regression tests; leave it off for a transcript you wrote or tested with. It can't change after import.",
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
      ? "You said these rows come from production, so each case is regression evidence, read again from the dataset each time."
      : NOT_PRODUCTION_NOTICE,
  transcriptNotice: (production) =>
    production
      ? "You said these transcripts come from production, so each case is regression evidence. They are yours, not Observe's."
      : NOT_PRODUCTION_NOTICE,
  // A Simulator project's sources are our own simulations, never production evidence.
  simulated: (kind) => `Simulated ${kind}, not production`,
  simulatedNotice: (kind) =>
    `These ${kind}s come from a Simulator project, so they make tests, never regression tests: no customer did this.`,
  notFound: "This import is not available.",
  loadError: "Couldn't load this import. Try again in a moment.",
  loading: "Loading this import…",
  stepEvidence: "1. Evidence",
  stepState: "2. Starting state",
  stepDraft: "3. Drafted scenarios",
  stepAdd: "4. Add to the suite",
  redactionReady: (total) =>
    total
      ? `${total} value${total === 1 ? "" : "s"} of personal data will be replaced with placeholders before anything becomes scenario text.`
      : "No personal data was found.",
  redactionStored: (total) =>
    total
      ? `${total} value${total === 1 ? "" : "s"} of personal data ${total === 1 ? "was" : "were"} replaced with placeholders when the transcripts were stored.`
      : "No personal data was found when the transcripts were stored.",
  shows: "What should this case's scenario be?",
  // No outcome recorded: nothing claims a failure or a success for the person.
  showsUnset: "Not set: the builder judges from the evidence",
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
    unset:
      "Nothing here records how it ended. Keeps: what the record shows. Makes: checks for what the builder can tell from it.",
    failure:
      "Keeps: the situation, what the user said and the failure. Makes: what should have happened, from the policy, not the agent's answer.",
    success:
      "Keeps: the workflow that worked, step by step. Makes: explicit checks; you still review the expected outcome.",
    partial:
      "Keeps: only what the record shows. Makes: checks for that part; the rest is labelled synthetic.",
  },
  // A dataset row's expected outcome is what a person wrote should happen: the
  // builder writes the expected behaviour from it, not from the policy alone.
  showsDetailExpected: {
    unset:
      "Nothing here records how it ended. Keeps: what the row shows. Makes: checks from the expected outcome column.",
    failure:
      "Keeps: the situation, what the user said and the failure. Makes: what should have happened, from the expected outcome column.",
    success:
      "Keeps: the workflow that worked, step by step. Makes: checks from the expected outcome column.",
    partial:
      "Keeps: only what the row shows. Makes: checks for that part, from the expected outcome column; the rest is labelled synthetic.",
  },
  startingState:
    "Observe doesn't hold the seed records the tools read first (an earlier refund). The draft marks them synthetic: the test is not an exact replay.",
  startingStateDataset:
    "A dataset row doesn't hold the seed records the tools read first (an earlier refund). The draft marks them synthetic: the test is not an exact replay.",
  startingStateTranscript:
    "A transcript doesn't hold the seed records the tools read first (an earlier refund). The draft marks them synthetic: the test is not an exact replay.",
  draft: (n) => `Draft ${n} scenario${n === 1 ? "" : "s"}`,
  drafting: (n) => `Drafting ${n} imported case${n === 1 ? "" : "s"}`,
  draftingBody:
    "Up to 90 minutes; you can close this meanwhile. Editing the environment before it ends blocks the drafted cases.",
  noneWaiting: "No case is waiting to be drafted.",
  waitingHint: (n) =>
    `${n} case${n === 1 ? " waits" : "s wait"} to be drafted. Pick what each should be, then draft.`,
  setAll: "Set every waiting case to",
  setAllPlaceholder: "Choose one for all",
  noneDrafted: "Draft scenarios first, then add them here.",
  drafted: "Drafted",
  blocked: "Blocked",
  added: "Added to the suite",
  unavailable: "Unavailable",
  pending: "Waiting",
  discarded: "Discarded",
  duplicate: "Already imported",
  // A case more than one source holds: the same source or the same conversation
  // imported again. It is proposed once.
  alsoFrom: (n) => `Also from ${n} more source${n === 1 ? "" : "s"}`,
  importedBefore: (name) =>
    name
      ? `Imported before; its scenario is ${name}.`
      : "Imported before, in an earlier import.",
  openExisting: "Open the existing case",
  newCheck: "New check added by this import",
  scenario: "Scenario",
  has: "Has:",
  missing: "Missing:",
  moreTurns: (n) => `Show ${n} more turn${n === 1 ? "" : "s"}`,
  fewerTurns: "Show fewer turns",
  untitled: (kind) =>
    `${kind[0].toUpperCase()}${kind.slice(1)} with no recorded conversation`,
  provenanceUnavailable:
    "Provenance unavailable: Observe no longer keeps this source.",
  provenanceUnavailableRow:
    "Provenance unavailable: the dataset no longer holds this row.",
  expectedOutcome: "Expected outcome",
  openInDataset: "Open in dataset",
  // The source's id, short, as Observe lists it: "Session 3f2a91c0".
  observeSource: (kind, id) =>
    `${kind[0].toUpperCase()}${kind.slice(1)} ${String(id).slice(0, 8)}`,
  openInObserve: "Open in Observe",
  changed: "The source changed since it was imported.",
  addClean: (n) => `Save ${n} to the suite`,
  addBeside: (n) => `Add ${n} to unsaved changes`,
  besideNotice:
    "These cases join the unsaved changes until you press Save in the draft bar. Until you save, Epoch can't write new variations.",
  besideAdded: (n) =>
    `${n} case${n === 1 ? " is" : "s are"} in the unsaved changes. Press Save in the draft bar to make ${n === 1 ? "it" : "them"} live. Until you save, Epoch can't write new variations.`,
  goToSave: "Go to Save",
  retry: "Retry",
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
  // The environment's imports a person can still finish (the Scenarios tab).
  draftImports: "Draft imports",
  importSource: (item) => {
    const n = item.case_count;
    const plural = n === 1 ? "" : "s";
    const gone = item.source_available ? "" : " (source deleted)";
    if (item.source_kind === "transcript") {
      const more = n > 1 ? ` and ${n - 1} more` : "";
      return item.source_name
        ? `${n} transcript${plural}: ${item.source_name}${more}`
        : `${n} transcript${plural}`;
    }
    if (item.source_kind === "dataset")
      return `${n} row${plural} from ${item.source_name}${gone}`;
    return `${n} ${item.source_kind}${plural} from ${item.source_name}${gone}`;
  },
  importStatus: {
    waiting: "waiting to be drafted",
    drafting: "drafting",
    drafted: "drafted, not added",
    partly_added: "partly added",
    blocked: "blocked: import again to retry",
  },
  imported: (when) => `imported ${when}`,
  resume: "Resume",
  discard: "Discard",
  discardBusy: "Drafting is running; discard it once drafting ends.",
  discardTitle: (item) => `Discard “${EVIDENCE_COPY.importSource(item)}”?`,
  discardBody: (item) => {
    const left = item.case_count - item.added_count;
    if (item.added_count)
      return `Its ${left} case${left === 1 ? "" : "s"} not added ${left === 1 ? "is" : "are"} discarded. The ${item.added_count} already added stay in the suite.`;
    if (item.source_kind === "transcript")
      return "Its cases are removed, and nothing joins the suite. The transcripts' stored text is deleted with it.";
    if (item.source_kind === "dataset")
      return `Its cases are removed, and nothing joins the suite. The rows stay in ${item.source_name}.`;
    return "Its cases are removed, and nothing joins the suite. The sources stay in Observe.";
  },
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

// A case's status as a StatusBadge role; a blocked case never reads as passed.
export const CASE_ROLE = {
  pending: "neutral",
  drafted: "info",
  added: "success",
  blocked: "danger",
  unavailable: "neutral",
  discarded: "neutral",
  duplicate: "neutral",
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

// A card's parts, each reason said once: parts that share one are joined
// ("Tool calls, Tool inputs and Tool outputs (…)"). Step 2 explains the
// starting state, so its reason is never repeated on a card.
export const partGroups = (item, parts) => {
  const groups = new Map();
  for (const part of parts) {
    const reason =
      part === "starting_state"
        ? ""
        : (item[part]?.reason ?? "").replace(/\.$/, "");
    const key = reason || `part:${part}`;
    if (!groups.has(key)) groups.set(key, { reason, labels: [] });
    groups.get(key).labels.push(PART_LABELS[part]);
  }
  return [...groups.values()].map(({ reason, labels }) => {
    const names =
      labels.length > 1
        ? `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`
        : labels[0];
    return reason ? `${names} (${reason})` : names;
  });
};

// Text at most `length` long, cut at the last word that fits.
export const cutAtWord = (text, length) => {
  if (!text || text.length <= length) return text;
  const head = text.slice(0, length + 1);
  const space = head.lastIndexOf(" ");
  return `${(space > 0 ? head.slice(0, space) : text.slice(0, length)).trimEnd()}…`;
};

// Labels record how a source ended; without them, no choice is made for the person.
// A dataset row's label columns are context the person picked (an intent, say), not
// an outcome, so a row never gets a default either.
export const caseShowsDefault = (item, kind) =>
  kind !== "dataset" &&
  ["present", "partial", "truncated"].includes(item.labels?.status)
    ? "failure"
    : "";

export const errorText = (error) =>
  EVIDENCE_COPY.errors[error?.error] ||
  error?.message ||
  EVIDENCE_COPY.genericError;
