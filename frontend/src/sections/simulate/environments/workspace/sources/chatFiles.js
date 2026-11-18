// Files attached in the builder chat. The conversation API doesn't know them,
// so their turns are kept in the page and spliced into the chat's own.

/**
 * `inserts` are `{at, turns}` in the order they were sent, `at` being the chat's
 * turn count when they were. Each lands after the turns that were there then.
 */
export const spliceTurns = (turns, inserts) => {
  const out = [...turns];
  let shift = 0;
  inserts.forEach(({ at, turns: added }) => {
    out.splice(Math.min(at + shift, out.length), 0, ...added);
    shift += added.length;
  });
  return out;
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export const CHAT_FILES_COPY = {
  attached: (names) => `Attached: ${names.join(", ")}`,
  reading: (n) =>
    `Reading ${n === 1 ? "the file" : `${n} files`}. Each is read on its own, so one that fails keeps the others and your message.`,
  propose: (text) =>
    `Here's what I'd change${text ? ` for “${text}”` : ""}. Expected answers stay with the grader; everything else cites where it came from.`,
  nothingNew: "These files are already in Sources, so nothing changes.",
  nothingToAdd:
    "None of those can be read here, so nothing changes. Attach a PDF, text, Markdown, CSV or JSON file.",
  status: {
    uploading: "Uploading",
    reading: "Reading",
    ready: "Ready",
    unreadable: "Can't read",
    unsupported: "Not supported",
    failed: "Upload failed",
  },
  failedNote: "Couldn't read these files. Try again in a moment.",
  rows: (parsed) =>
    `${parsed.accepted.toLocaleString()} of ${parsed.rows.toLocaleString()} rows`,
  passages: (n) => plural(n, "passage"),
  added: (n) => `Added ${plural(n, "change")} to your unsaved changes.`,
  couldntApply: (n) => `${plural(n, "blocked item")} went to Couldn't apply.`,
  savedAs: (label) => `Saved as ${label} from Review changes`,
  rejected:
    "Dismissed the proposed changes. The files stay in Sources as Not saved yet.",
};
