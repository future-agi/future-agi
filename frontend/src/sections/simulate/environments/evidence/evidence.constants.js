import { MAX_EVIDENCE_SOURCES } from "src/api/simulate-environments/evidenceImports";

// What the import review says. One term per thing: an "import" holds "cases",
// each from one Observe session or call; the builder "drafts" a scenario for a
// case; a person "adds" drafted cases to the suite.

export const IMPORT_PARAM = "import";

export const EVIDENCE_ACTION_ID = "create-environment-test";

export const EVIDENCE_COPY = {
  action: "Create environment test",
  actionTooMany: `Pick at most ${MAX_EVIDENCE_SOURCES} to import at once`,
  actionAll: "Tick them one by one; Select all isn't available here",
  actionNoProject: "Open a project to import its evidence",
  dialogTitle: "Create environment test",
  dialogBody: (count, kind) =>
    `${count} ${kind}${count === 1 ? "" : "s"} from this project become a draft import. Nothing runs, and nothing joins the suite until you add it.`,
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
    "Pick sessions or calls in Observe, then choose Create environment test. The import opens here for review.",
  openObserve: "Open Observe",
  title: "Review imported evidence",
  subtitle: (count, kind) =>
    `${count} ${kind}${count === 1 ? "" : "s"} from Observe`,
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
  shows: "What should this case's scenario be?",
  // No outcome recorded: nothing claims a failure or a success for the person.
  showsUnset: "Not set: the builder judges from the evidence",
  showsOptions: {
    failure: "Reproduce the failure as a regression test",
    success: "Keep this success as a regression test",
    partial: "A regression test from partial evidence",
  },
  // A Simulator project's sources are not production: they make tests, never
  // regression tests.
  showsOptionsSimulated: {
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
  startingState:
    "Observe doesn't hold the seed records the tools read first (an earlier refund). The draft marks them synthetic: the test is not an exact replay.",
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
};

export const PART_LABELS = {
  transcript: "Transcript",
  tool_calls: "Tool calls",
  tool_inputs: "Tool inputs",
  tool_outputs: "Tool outputs",
  labels: "Labels",
  starting_state: "Starting state",
};

// A case's status as a StatusBadge role; a blocked case never reads as passed.
export const CASE_ROLE = {
  pending: "neutral",
  drafted: "info",
  added: "success",
  blocked: "danger",
  unavailable: "neutral",
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
export const caseShowsDefault = (item) =>
  ["present", "partial", "truncated"].includes(item.labels?.status)
    ? "failure"
    : "";

export const errorText = (error) =>
  EVIDENCE_COPY.errors[error?.error] ||
  error?.message ||
  EVIDENCE_COPY.genericError;
