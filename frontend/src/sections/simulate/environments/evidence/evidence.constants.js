import { MAX_EVIDENCE_SOURCES } from "src/api/simulate-environments/evidenceImports";

// What the import review says. One term per thing: an "import" holds "cases",
// each from one Observe session or call; the builder "drafts" a scenario for a
// case; a person "adds" drafted cases to the suite.

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
    "Pick sessions or calls in Observe, then choose Create environment test. The import opens here for review.",
  openObserve: "Open Observe",
  title: "Review imported evidence",
  subtitle: (count, kind) =>
    `${count} ${kind}${count === 1 ? "" : "s"} from Observe`,
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
  shows: "What the record shows",
  showsOptions: {
    failure: "A failure",
    success: "A correct path",
    partial: "Partial evidence",
  },
  startingState:
    "Observe never records the records the agent's tools read before the conversation (an earlier refund, an order's age). The draft seeds what the evidence shows and labels everything it assumes as synthetic. If you have the real record, add it to the scenario after it is drafted: the test is not an exact replay.",
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

export const errorText = (error) =>
  EVIDENCE_COPY.errors[error?.error] ||
  error?.message ||
  EVIDENCE_COPY.genericError;
