// World & tools → Sources: the files an environment is built from, what each
// is used as and who reads it in a run. Ids match the sources API.

const MB = 1024 * 1024;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export const ROLES = [
  {
    id: "policy",
    label: "Rules",
    option: "Rules to grade against",
    note: "Sentences like “refunds over $200 need approval” become rules checked on every run.",
  },
  {
    id: "reference",
    label: "Background",
    option: "Background for the agent",
    note: "The agent can look things up in it. Nothing is graded from it.",
  },
  {
    id: "seed_records",
    label: "Records",
    option: "Records for the world",
    note: "Its rows fill the world's tables, which is what the agent's tools return.",
  },
  {
    id: "evaluation_reference",
    label: "Grader answers",
    option: "Answers for the grader",
    note: "Expected answers. Hidden from the agent and the simulated customer.",
  },
];

export const VISIBILITY = [
  {
    id: "agent",
    label: "The agent, directly",
    note: "The agent reads the file itself, like background it can look up.",
  },
  {
    id: "tool",
    label: "Through tools only",
    note: "The agent never sees the file, only what the world's tools return from it.",
  },
  {
    id: "evaluator",
    label: "Grader only",
    note: "Used to grade. It never reaches the agent or the customer.",
  },
];

// Connectors that aren't built yet: one quiet line in the Add source menu.
export const UNAVAILABLE_CONNECTORS = [
  { id: "drive", name: "Google Drive", icon: "logos:google-drive" },
  { id: "notion", name: "Notion", icon: "logos:notion-icon" },
  { id: "confluence", name: "Confluence", icon: "logos:confluence" },
  { id: "salesforce", name: "Salesforce", icon: "logos:salesforce" },
];

// The same import from code, as the Add source menu copies it.
export const dataImportCommand = (envId, source) =>
  `fi api data import ./${source?.name || "orders.csv"} --environment ${envId || "<env>"} --role ${source?.role || "seed_records"} --visibility ${source?.visibility || "tool"} --preview`;

// In the world once accepted; anything else is still waiting or can't be used.
export const isSaved = (source) =>
  source.status === "ready" || source.status === "rows_rejected";

// Each participant's view of the saved files: who reads which, and how.
export const participantFilesOf = (sources) => {
  const files = { agent: [], caller: [], evaluator: [] };
  sources.forEach((source) => {
    if (source.visibility === "agent")
      files.agent.push({ source, how: "in its context" });
    if (source.visibility === "tool")
      files.agent.push({ source, how: "only through the world's tools" });
    files.evaluator.push({
      source,
      how:
        source.visibility === "evaluator"
          ? "only the grader"
          : "to grade against",
    });
  });
  return files;
};

// Why a pick can't be uploaded under the route's limits, or null.
export const overUploadLimit = (files, limits) => {
  if (!limits) return null;
  const total = files.reduce((sum, file) => sum + file.size, 0);
  const fits =
    files.length <= limits.max_files &&
    total <= limits.max_total_bytes &&
    files.every((file) => file.size <= limits.max_file_bytes);
  return fits
    ? null
    : `Upload up to ${limits.max_files} files, ${Math.round(limits.max_file_bytes / MB)} MB a file and ${Math.round(limits.max_total_bytes / MB)} MB in all.`;
};

export const SOURCES_COPY = {
  title: (n) => `Sources · ${n}`,
  subtitle: {
    empty:
      "The files and data this world is built from. None yet: attach one in builder chat, or add it here.",
    notSaved: (n) =>
      `${n} not saved yet. Accept the builder's proposal to add ${n === 1 ? "it" : "them"} to the world.`,
    saved: (snapshot, notSaved) =>
      [
        snapshot && `Snapshot ${snapshot.replace(/^sha256:/, "").slice(0, 12)}`,
        notSaved && `${notSaved} not saved yet`,
      ]
        .filter(Boolean)
        .join(" · "),
  },
  add: "Add source",
  columns: [
    "Source",
    "Type",
    "Used as",
    "Who reads it",
    "State",
    "Version",
    "Used by",
    "",
  ],
  state: {
    reading: "Reading…",
    ready: "Ready",
    not_saved: "Not saved yet",
    unreadable: "Can't read",
    unsupported: "Not supported",
  },
  // A problem says what in the cell and how to fix it on hover.
  problem: {
    rows_rejected: (note) => ({
      short: note?.replace(/ of \d+ rows/, " rows") || "Rows rejected",
      hint: "Open it to see which rows and why.",
    }),
    unreadable: (note) => ({
      short: "Can't read",
      hint: note || "No text to read. Upload a text version instead.",
    }),
    unsupported: (note) => ({
      short: "Not supported",
      hint: `${note || "This file type can't be read."} Export it as a PDF and upload that.`,
    }),
  },
  yourCopy: "your copy",
  version: (n) => `v${n}`,
  usedByNone: "Nothing yet",
  remove: (name) => `Remove ${name}`,
  removeHint: (saved) =>
    saved
      ? "Remove from this environment. New versions stop using it; past runs keep it."
      : "Remove. It isn't in the world yet.",
  removeTitle: (name) => `Remove ${name}?`,
  removeBody: (saved) =>
    saved
      ? "New versions of this environment stop using it. Past runs still show it. You can restore it from Removed."
      : "It isn't in the world yet. You can restore it from Removed.",
  removeConfirm: "Remove",
  personal: "Personal data · choose how to handle it",
  choose: "Choose",
  changedUpstream: "Changed in the Knowledge base since you added it",
  reviewRefresh: "Review refresh",
  inRun: (files) =>
    `In a run · agent ${files.agent.length} · caller ${files.caller.length} · grader ${plural(files.evaluator.length, "file")}`,
  inRunDetail: (files) =>
    [
      ...[
        ["agent", "Agent"],
        ["caller", "Caller"],
        ["evaluator", "Grader"],
      ].map(
        ([key, label]) =>
          `${label}: ${files[key].length ? files[key].map(({ source, how }) => `${source.name} (${how})`).join(", ") : "none"}`,
      ),
      "From each file's Who reads it, set by how it's used and changeable on the file.",
    ].join("\n"),
  removed: (n) => `Removed · ${n}`,
  removedNote: "New versions don't use it. Past runs still show it.",
  restore: "Restore",
  restored: (name) => `Restored ${name}.`,
  readFailed: "Couldn't read these files. Try again in a moment.",
  menu: {
    upload: "Upload files",
    uploadLimit: (limits) =>
      limits ? `up to ${Math.round(limits.max_file_bytes / MB)} MB` : "",
    uploadHint: (limits) =>
      limits
        ? `${limits.formats.map((ext) => ext.toUpperCase()).join(", ")} · up to ${Math.round(limits.max_file_bytes / MB)} MB a file · ${limits.max_files} files or ${Math.round(limits.max_total_bytes / MB)} MB at a time`
        : "",
    kb: "From the Knowledge base",
    kbHint: "Copies a snapshot of the revision you pick. It isn't linked live.",
    code: "From code",
    codeNote: "copy the command",
    copied: "Command copied. Run it in your terminal.",
    copyFailed: "Couldn't copy the command.",
    unavailable: "Not available yet",
  },
};
