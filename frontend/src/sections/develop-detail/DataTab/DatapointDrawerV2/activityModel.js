// RV#22: the Activity tab's filters, day groups and run bursts. Pure.

/** Filter chips, in order. `types` = the N8 event types each one keeps. */
export const ACTIVITY_FILTERS = [
  { key: "all", label: "All" },
  {
    key: "runs",
    label: "Runs",
    types: ["run"],
    none: "No runs on this row yet.",
  },
  {
    key: "edits",
    label: "Edits",
    // Row created / deleted / restored, and every cell change (`edit`).
    types: ["edit", "created", "deleted", "restored"],
    none: "No edits on this row yet.",
  },
  {
    key: "annotations",
    label: "Annotations",
    // Scores only, so the chip matches the Annotations tab's count
    // (queue adds still show under All).
    types: ["score"],
    none: "No annotations on this row yet.",
  },
  {
    key: "splits",
    label: "Splits",
    types: ["splits"],
    none: "No split changes on this row yet.",
  },
];

const filterOf = (key) =>
  ACTIVITY_FILTERS.find((f) => f.key === key) ?? ACTIVITY_FILTERS[0];

/** Events kept by a filter key (`all` keeps every event). */
export function filterActivity(events, key) {
  const f = filterOf(key);
  const list = events ?? [];
  return f.types ? list.filter((e) => f.types.includes(e?.type)) : list;
}

/** `{all: 17, runs: 9, edits: 3, …}` */
export function activityCounts(events) {
  return Object.fromEntries(
    ACTIVITY_FILTERS.map((f) => [f.key, filterActivity(events, f.key).length]),
  );
}

const DAY = 86400000;
const startOfDay = (t) => {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
};

/** `Today` · `Yesterday` · `This week` · `Last week` · `Older` (local days). */
export function dayBucket(iso, now = Date.now()) {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "Older";
  const days = Math.round((startOfDay(now) - startOfDay(t)) / DAY);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "This week";
  if (days < 14) return "Last week";
  return "Older";
}

/**
 * Consecutive runs (3 or more, newest first) fold into one
 * `{type: "runs", runs, passed, failed, at}` event: `9 runs · 6 passed`.
 */
export function collapseRunBursts(events, min = 3) {
  const out = [];
  let burst = [];
  const flush = () => {
    if (burst.length >= min) {
      out.push({
        type: "runs",
        runs: burst,
        passed: burst.filter((r) => r.passed === true).length,
        failed: burst.filter((r) => r.passed === false).length,
        at: burst[0].at,
      });
    } else out.push(...burst);
    burst = [];
  };
  (events ?? []).forEach((e) => {
    if (e?.type === "run") burst.push(e);
    else {
      flush();
      out.push(e);
    }
  });
  flush();
  return out;
}

/**
 * Day groups in the order given (newest first), each with its run bursts
 * folded: `[{key: "Today", title: "Today", events: […]}, …]`.
 */
export function groupActivity(events, now = Date.now()) {
  const groups = [];
  (events ?? []).forEach((e) => {
    const title = dayBucket(e?.at, now);
    const last = groups[groups.length - 1];
    if (last && last.title === title) last.events.push(e);
    else groups.push({ key: `${title}-${groups.length}`, title, events: [e] });
  });
  return groups.map((g) => ({ ...g, events: collapseRunBursts(g.events) }));
}

// ------------------------------------------------------------------ W4-1
// Comments in the Activity tab (additive: the exports above are
// unchanged). Each N8 `comment` event is one thread on the row.

/** All · Comments · Runs · Edits · Annotations · Splits. */
export const COLLAB_ACTIVITY_FILTERS = [
  ACTIVITY_FILTERS[0],
  {
    key: "comments",
    label: "Comments",
    types: ["comment"],
    none: "No comments on this row yet.",
  },
  ...ACTIVITY_FILTERS.slice(1),
];

const collabFilterOf = (key) =>
  COLLAB_ACTIVITY_FILTERS.find((f) => f.key === key) ??
  COLLAB_ACTIVITY_FILTERS[0];

/** Events kept by a chip of the list with Comments. */
export function filterCollabActivity(events, key) {
  const f = collabFilterOf(key);
  const list = events ?? [];
  return f.types ? list.filter((e) => f.types.includes(e?.type)) : list;
}

/** `{all, comments, runs, edits, annotations, splits}` */
export function collabActivityCounts(events) {
  return Object.fromEntries(
    COLLAB_ACTIVITY_FILTERS.map((f) => [
      f.key,
      filterCollabActivity(events, f.key).length,
    ]),
  );
}

/** The Comments sub-toggle: Open · Resolved · All. */
export const COMMENT_STATUS_FILTERS = [
  { key: "open", label: "Open" },
  { key: "resolved", label: "Resolved" },
  { key: "all", label: "All" },
];

/**
 * `{open, resolved, all}` threads from the N8 comment events, counted the
 * way the thread list filters them (a deleted root is never open).
 */
export function commentStatusCounts(events) {
  const c = (events ?? []).filter((e) => e?.type === "comment");
  return {
    open: c.filter((e) => !e.resolved && !e.deleted).length,
    resolved: c.filter((e) => e.resolved).length,
    all: c.length,
  };
}

/**
 * `{open, resolved, all}` from the comments read's threads (`[{root,
 * replies}]`), counted the way the thread list filters them
 * (commentModel.filterThreads): the counts when comments show without the
 * row history (no N8 comment events to count).
 */
export function threadStatusCounts(threads) {
  const t = threads ?? [];
  return {
    open: t.filter((x) => !x?.root?.deleted && !x?.root?.resolved).length,
    resolved: t.filter((x) => x?.root?.resolved).length,
    all: t.length,
  };
}

// ------------------------------------------------------------ history reads
// The axios layer rejects with the response body plus `statusCode` (no
// status when the server never answered) and Axios' `transportCode`.

const statusOf = (error) =>
  error?.statusCode ?? error?.response?.status ?? null;

const serverText = (error) => {
  const body = error?.response?.data ?? error;
  return (
    [body?.detail, body?.error, body?.result, body?.message].find(
      (t) => typeof t === "string" && t.trim() !== "",
    ) ?? null
  );
};

/**
 * One plain sentence for a failed history read: the server's own reason
 * for a refusal (403 `detail`), a fixed sentence for a server failure or
 * when the server never answered.
 */
export function activityErrorReason(error) {
  if (!error) return null;
  const status = statusOf(error);
  if (!status) {
    const code = String(error?.transportCode ?? error?.code ?? "");
    if (code === "ECONNABORTED" || code === "ETIMEDOUT")
      return "The server took too long to answer. Try again.";
    if (code === "ERR_NETWORK" || /network/i.test(String(error?.message)))
      return "The server didn't answer. Check your connection and try again.";
    return "Something went wrong. Try again.";
  }
  if (status >= 500) return "Something went wrong on our side. Try again.";
  const said = serverText(error);
  if (status === 403)
    return said ?? "You don't have access to this row's activity.";
  if (status === 404)
    return said ?? "This row wasn't found. It may have been deleted.";
  return said ?? "The server refused this request.";
}

/**
 * An older page refused as a bad request: the link to it expired (after a
 * day) or no longer matches the list, so the list is read again from the
 * newest page.
 */
export const activityListExpired = (error) => statusOf(error) === 400;
