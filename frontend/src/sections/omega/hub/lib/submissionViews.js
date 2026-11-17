// My submissions' four views: what needs the person (the default), what
// waits on someone else, what is published, and everything. Nothing is
// removed: every submission the server lists stays under All.

/** The views, in the order the segmented control shows them. */
export const SUBMISSION_VIEWS = [
  { value: "needs", label: "Needs you" },
  { value: "waiting", label: "Waiting" },
  { value: "published", label: "Published" },
  { value: "all", label: "All" },
];

export const DEFAULT_VIEW = "needs";

/** The view a `?view=` value names; a missing or unknown one reads the default. */
export const viewOf = (param) =>
  SUBMISSION_VIEWS.some((view) => view.value === param) ? param : DEFAULT_VIEW;

// The publisher moves these next. The server never moves them again
// (omega_hub/services/states.py): the publisher answers them with a new
// upload (its own words: "Fix each finding and publish again", "Answer by
// publishing a new upload"). Once a newer upload of the same entry exists,
// the old one needs nothing more. A withdrawn upload answers nothing.
const YOURS = new Set(["checks_failed", "changes_requested"]);

// Someone else moves these next: the automated checks (received, checking),
// a maintainer (in_review, and check_error, whose checks a maintainer runs
// again), or, while a self-hosted server runs no Hub checker, an
// administrator.
const WAITING = new Set(["received", "checking", "check_error", "in_review"]);

const entryOf = (row) => {
  const name = row.name || row.hints?.name;
  return name ? `${row.publisher}/${name}` : null;
};

// Date.parse, not string order: Python's isoformat drops ".000000".
const createdOf = (row) => Date.parse(row.createdAt) || 0;

/**
 * The upload that answers another, over `rows`: for a failed or
 * change-requested upload, the newest upload of the same entry when it is
 * newer; otherwise null. A withdrawn upload answers nothing, and one the
 * page cannot tie to an entry (no name) is never answered.
 * @param {object[]} rows
 * @returns {(row: object) => object | null}
 */
export function answeringUpload(rows) {
  const newest = new Map();
  for (const row of rows) {
    const entry = entryOf(row);
    if (!entry || row.status === "withdrawn") continue;
    const seen = newest.get(entry);
    if (!seen || createdOf(row) > createdOf(seen)) newest.set(entry, row);
  }
  return (row) => {
    if (!YOURS.has(row.status)) return null;
    const entry = entryOf(row);
    const latest = entry ? newest.get(entry) : undefined;
    return latest && createdOf(latest) > createdOf(row) ? latest : null;
  };
}

/**
 * A test for "a newer upload of the same entry answers this one", over
 * `rows`: true for a failed or change-requested upload only. One the page
 * cannot tie to an entry (no name) is never answered.
 * @param {object[]} rows
 * @returns {(row: object) => boolean}
 */
export function answeredByNewer(rows) {
  const answering = answeringUpload(rows);
  return (row) => Boolean(answering(row));
}

/**
 * The submissions only the person can move, in the order given: every
 * failed or change-requested upload no newer upload of the same entry has
 * answered.
 * @param {object[]} rows
 */
export function needingYou(rows) {
  const answered = answeredByNewer(rows);
  return rows.filter((row) => YOURS.has(row.status) && !answered(row));
}

/**
 * Each view's rows, in the server's order (newest first).
 * @param {object[]} rows
 * @returns {{needs: object[], waiting: object[], published: object[], all: object[]}}
 */
export function submissionViews(rows) {
  return {
    needs: needingYou(rows),
    waiting: rows.filter((row) => WAITING.has(row.status)),
    published: rows.filter((row) => row.status === "approved"),
    all: rows,
  };
}

/** Who moves a submission next, in the words its row and its page use. */
export const NEXT_STEP = Object.freeze({
  you: Object.freeze({ who: "you", label: "Your turn" }),
  checks: Object.freeze({ who: "checks", label: "Waiting on the checks" }),
  maintainer: Object.freeze({
    who: "maintainer",
    label: "Waiting on a maintainer",
  }),
  administrator: Object.freeze({
    who: "administrator",
    label: "Waiting on an administrator",
  }),
  answered: Object.freeze({
    who: "answered",
    label: "Answered by a newer upload",
  }),
  none: Object.freeze({ who: "none", label: "Nothing to do" }),
  // A stalled submission on Cloud, or while the server's mode is not known:
  // there is no next step to name.
  stalled: Object.freeze({ who: "stalled", label: "" }),
});

/**
 * Who moves `row` next. `stalled`: this server runs no Hub checker, so a
 * received, checking or check_error submission cannot move
 * (lib/uploadsChecked.js). On a self-hosted server (`selfHosted`) it waits
 * on an administrator, who starts the checker; on Cloud, and while the
 * server's mode is not known, it names no next step (NEXT_STEP.stalled), and
 * nothing sends the person to an administrator. `answered`: a newer upload of
 * the same entry answers it (answeredByNewer, answeringUpload).
 * @param {object} row
 * @param {{stalled?: boolean, answered?: boolean, selfHosted?: boolean}} [options]
 */
export function nextStep(
  row,
  { stalled = false, answered = false, selfHosted = false } = {},
) {
  if (YOURS.has(row.status))
    return answered ? NEXT_STEP.answered : NEXT_STEP.you;
  if (!WAITING.has(row.status)) return NEXT_STEP.none;
  if (stalled) return selfHosted ? NEXT_STEP.administrator : NEXT_STEP.stalled;
  if (row.status === "received" || row.status === "checking")
    return NEXT_STEP.checks;
  return NEXT_STEP.maintainer;
}
