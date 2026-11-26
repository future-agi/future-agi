import { plural } from "./sources.constants";

// Review proposed changes: what the builder read in uploaded files, grouped
// by what each change touches (the same areas as Review changes).

export const PROPOSAL_KINDS = {
  rule: { icon: "solar:document-text-linear" },
  table: { icon: "solar:database-linear" },
  scenario: { icon: "solar:layers-minimalistic-linear" },
  missing: { icon: "solar:danger-triangle-linear" },
  conflict: { icon: "solar:danger-triangle-linear" },
  hidden: { icon: "solar:eye-closed-linear" },
  unreadable: { icon: "solar:danger-triangle-linear" },
  actor: { icon: "solar:users-group-rounded-linear" },
  tool: { icon: "solar:widget-4-linear" },
  state: { icon: "solar:database-linear" },
  reference: { icon: "solar:book-linear" },
  personal: { icon: "solar:shield-user-linear" },
};

const GROUPS = [
  { group: "Rules", kinds: ["conflict", "rule"] },
  { group: "Starting state and records", kinds: ["table", "state", "missing"] },
  { group: "Tools", kinds: ["tool"] },
  { group: "Actors", kinds: ["actor"] },
  { group: "Scenarios", kinds: ["scenario"] },
  { group: "Background for the agent", kinds: ["reference"] },
  { group: "Personal data, your choice", kinds: ["personal"] },
  {
    group: "Kept from the agent, and problems",
    kinds: ["hidden", "unreadable"],
  },
];

// A choice, a note or a problem isn't a change to add.
export const isPickable = (item) =>
  !item.blocking && !["hidden", "unreadable", "personal"].includes(item.kind);

export const groupProposals = (items) =>
  GROUPS.map(({ group, kinds }) => ({
    group,
    items: items.filter((item) => kinds.includes(item.kind)),
  })).filter(({ items: inGroup }) => inGroup.length > 0);

/**
 * What "Add N changes" adds: the checked items that can be added. Every
 * blocked item stays out and comes back as failed, checked or not.
 */
export const selectionResult = (items, checked) => {
  const on = new Set(checked);
  return {
    accepted: items.filter((item) => isPickable(item) && on.has(item.id)),
    failed: items.filter((item) => item.blocking),
  };
};

const filesOf = (item) =>
  item.options?.map((option) => option.sourceId) || [
    item.sourceId || item.cite?.sourceId,
  ];

/**
 * Each file stays "reading" until the live summary says it was read, then its
 * items show. Derived from the cache every render, so closing and reopening
 * mid-read neither repeats items nor reads a finished file again. A file no
 * longer in the summary was removed: its row and items leave the review.
 * Without a summary yet (`liveSources` undefined) the prepared status stands.
 */
export const readProgress = (prepared, liveSources) => {
  const live = liveSources
    ? new Map(liveSources.map((source) => [source.source_id, source.status]))
    : null;
  const statusOf = (source) =>
    live ? live.get(source.source_id) : source.status;
  const files = prepared?.ui?.sources || [];
  const gone = new Set(
    files
      .filter((source) => !statusOf(source))
      .map((source) => source.source_id),
  );
  const reading = files.filter((source) => statusOf(source) === "reading");
  const pending = new Set(reading.map((source) => source.source_id));
  return {
    reading,
    items: (prepared?.ui?.items || []).filter((item) =>
      filesOf(item).every((id) => !pending.has(id) && !gone.has(id)),
    ),
  };
};

// The prepare responses a review covers, one accept each.
export const batchesOf = (prepared) => prepared.batches || [prepared];

/**
 * A second prepare while a review is open joins it, so both batches can be
 * added. Items the open review already has (same id) aren't repeated, and
 * files read again leave their older batch, which goes once it has none left.
 */
export const mergePrepared = (current, next) => {
  const fresh = new Set(next.ui.sources.map((source) => source.source_id));
  const kept = batchesOf(current)
    .map((batch) => ({
      ...batch,
      ui: {
        ...batch.ui,
        sources: batch.ui.sources.filter(
          (source) => !fresh.has(source.source_id),
        ),
        items: batch.ui.items.filter((item) =>
          filesOf(item).every((id) => !fresh.has(id)),
        ),
      },
    }))
    .filter((batch) => batch.ui.sources.length > 0);
  const items = kept.flatMap((batch) => batch.ui.items);
  const known = new Set(items.map((item) => item.id));
  const added = {
    ...next,
    ui: {
      ...next.ui,
      items: next.ui.items.filter((item) => !known.has(item.id)),
    },
  };
  return {
    ...current,
    batches: [...kept, added],
    ui: {
      ...current.ui,
      readMs: Math.max(current.ui.readMs, next.ui.readMs),
      sources: [
        ...kept.flatMap((batch) => batch.ui.sources),
        ...next.ui.sources,
      ],
      items: [...items, ...added.ui.items],
    },
  };
};

// The sources API knows two modes; "Keep" and "No personal data present" both leave the values as they are.
const PERSONAL_MODE = { replace: "synthetic_replace" };
const SYNTHETIC_COLUMNS = ["replace", "mask"];

// Custom sends synthetic values once any column is replaced or masked.
const personalModeOf = (choice, privacy = {}) => {
  if (choice === "custom")
    return Object.values(privacy).some((mode) =>
      SYNTHETIC_COLUMNS.includes(mode),
    )
      ? "synthetic_replace"
      : "none";
  return PERSONAL_MODE[choice] || "none";
};

const statementIdOf = (draft, sourceId, text) =>
  draft.sources
    .find((source) => source.pin.source_id === sourceId)
    ?.statements.find((statement) => statement.text === text)?.statement_id;

/**
 * `buildSourceReview` decisions for the accepted items and each file's
 * personal-data choice. `privacy` is each file's column map, read for a
 * custom choice.
 */
export const reviewDecisions = (
  draft,
  accepted,
  choices = {},
  privacy = {},
) => {
  const tables = new Set(
    accepted
      .filter((item) => item.kind === "table")
      .map((item) => item.cite?.sourceId),
  );
  const indexesOf = (list, keep) =>
    list.flatMap((entry, index) => (keep(entry) ? [index] : []));
  const ruleOf = (item) => {
    if (item.kind === "rule")
      return { id: statementIdOf(draft, item.cite?.sourceId, item.rule) };
    if (item.kind === "conflict" && item.pick)
      return { id: statementIdOf(draft, item.pick.sourceId, item.pick.rule) };
    return {};
  };
  const rules = accepted
    .map((item) => ({ ...ruleOf(item), edited: item.edited }))
    .filter((rule) => rule.id);
  return {
    acceptedRuleIds: rules.map((rule) => rule.id),
    editedRules: Object.fromEntries(
      rules.filter((rule) => rule.edited).map((rule) => [rule.id, rule.edited]),
    ),
    actorEdits: Object.fromEntries(
      accepted
        .filter((item) => item.kind === "actor" && item.edited)
        .map((item) => [item.id, item.edited]),
    ),
    acceptedMappingIndexes: indexesOf(
      draft.proposals.field_mappings,
      (proposal) => tables.has(proposal.source_id),
    ),
    acceptedRelationshipIndexes: indexesOf(
      draft.proposals.relationships,
      ({ relationship }) =>
        tables.has(relationship.from_source_id) &&
        tables.has(relationship.to_source_id),
    ),
    personalDataModes: Object.fromEntries(
      Object.entries(choices).map(([sourceId, choice]) => [
        sourceId,
        personalModeOf(choice, privacy[sourceId]),
      ]),
    ),
  };
};

export const PROPOSALS_COPY = {
  title: "Review proposed changes",
  subtitle: (names) =>
    `What the builder read in ${names.join(", ")}, nothing changes until you add it`,
  cardTitle: "Proposed changes",
  cardSubtitle: "Nothing changes until you add them.",
  reopen: "Review proposed changes",
  reading: (name) => `Reading ${name}…`,
  inferred: "· inferred",
  from: (ref) => `From ${ref}`,
  editRule: "Edit rule",
  edit: "Edit",
  editHint: "Changes the rule we'll add. The file stays as it is.",
  editOtherHint: "Changes what we'll add. The file stays as it is.",
  edited: (label, value) => `${label}: ${value} · edited`,
  editDone: "Done",
  ruleLabel: "Rule",
  conflictOption: (option) => `${option.label} applies: ${option.rule}`,
  missingKept: "Kept, flagged until resolved",
  missingAffects: (n) =>
    `${n} ${n === 1 ? "scenario uses" : "scenarios use"} these rows`,
  supply: "Supply the records",
  leaveOut: "Leave those rows out",
  leftOut: "Left those rows out.",
  undo: "Undo",
  personalReplaced: "Personal fields replaced",
  reference: {
    file: "File",
    usedAs: "Used as",
    usedAsValue: "Background, the agent can look things up in it",
    rules: "Rules",
    rulesValue: "None stated, so nothing is graded from it",
    read: "Read",
    readValue: (read) =>
      read.passages.length
        ? `${plural(read.sections, "section")} · ${read.words.toLocaleString()} words`
        : "No readable text",
    section: "Section",
    says: "What it says",
    showAll: (n) => `Show all ${n} lines`,
    showFewer: "Show fewer",
  },
  footer: {
    choose: "Choose how to handle personal data first",
    blocked: "Blocked items stay out",
    saved: "Saved from Review changes",
    savedAs: (label) => `Saved as ${label} from Review changes`,
  },
  reject: "Reject",
  done: "Done",
  add: (n) => `Add ${plural(n, "change")}`,
  // Counted against the button: of the `count` on it, the blocked ones didn't apply.
  // `count` is the button's number: blocked items were never on it.
  added: (count, failed) =>
    `Added ${failed ? `${count} of ${plural(count + failed, "change")}` : plural(count, "change")} to your unsaved changes${failed ? ` · ${failed} couldn't apply` : ""}. Review and save them to make the version.`,
  addFailed: "Couldn't add these changes. Try again in a moment.",
  reviewChanges: "Review changes",
};
