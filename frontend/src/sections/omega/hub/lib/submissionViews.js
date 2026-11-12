// My submissions' three views: what needs the person (the default), what is
// published, and everything. Nothing is removed: every submission the server
// lists stays under All.

/** The views, in the order the segmented control shows them. */
export const SUBMISSION_VIEWS = [
  { value: "needs", label: "Needs you" },
  { value: "published", label: "Published" },
  { value: "all", label: "All" },
];

export const DEFAULT_VIEW = "needs";

/** The view a `?view=` value names; a missing or unknown one reads the default. */
export const viewOf = (param) =>
  SUBMISSION_VIEWS.some((view) => view.value === param) ? param : DEFAULT_VIEW;

// Open: still in checks or review, or waiting on the publisher. Approved,
// rejected, withdrawn and replaced submissions are done.
const OPEN = new Set([
  "received",
  "checking",
  "check_error",
  "in_review",
  "checks_failed",
  "changes_requested",
]);

// The server never moves these again (omega_hub/services/states.py): the
// publisher answers them with a new upload (its own words: "Fix each finding
// and publish again", "Answer by publishing a new upload"). Once a newer
// upload of the same entry exists, the old one needs nothing more. A
// withdrawn upload answers nothing.
const ANSWERED_BY_A_NEWER_UPLOAD = new Set([
  "checks_failed",
  "changes_requested",
]);

const entryOf = (row) => {
  const name = row.name || row.hints?.name;
  return name ? `${row.publisher}/${name}` : null;
};

// Date.parse, not string order: Python's isoformat drops ".000000".
const createdOf = (row) => Date.parse(row.createdAt) || 0;

/**
 * The submissions that need the person, in the order given: every open one,
 * except a failed or change-requested upload a newer upload of the same entry
 * has answered. One the page cannot tie to an entry (no name) stays.
 * @param {object[]} rows
 */
export function needingYou(rows) {
  const newest = new Map();
  for (const row of rows) {
    const entry = entryOf(row);
    if (!entry || row.status === "withdrawn") continue;
    newest.set(entry, Math.max(newest.get(entry) ?? -Infinity, createdOf(row)));
  }
  return rows.filter((row) => {
    if (!OPEN.has(row.status)) return false;
    if (!ANSWERED_BY_A_NEWER_UPLOAD.has(row.status)) return true;
    const entry = entryOf(row);
    return !entry || !(newest.get(entry) > createdOf(row));
  });
}

/**
 * Each view's rows, in the server's order (newest first).
 * @param {object[]} rows
 * @returns {{needs: object[], published: object[], all: object[]}}
 */
export function submissionViews(rows) {
  return {
    needs: needingYou(rows),
    published: rows.filter((row) => row.status === "approved"),
    all: rows,
  };
}
