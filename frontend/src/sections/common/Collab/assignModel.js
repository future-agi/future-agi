// W4-2 (Lane B, D28): row assignments, pure. Due dates are local calendar
// days; overdue is measured from the end of the due day.

const DAY = 86400000;
const startOfDay = (ms) => {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
};
const endOfDay = (ms) => startOfDay(ms) + DAY - 1;

/** `Fri 24 Oct` (the year when it is not the current one). */
export function dueLabel(iso, now = Date.now()) {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "";
  const d = new Date(t);
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  const day = d.toLocaleDateString("en-GB", { weekday: "short" });
  const date = d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
  return `${day} ${date}`;
}

/** Whole days past the due day (0 when not overdue or done). */
export function overdueDays(assignment, now = Date.now()) {
  if (!assignment || assignment.status !== "open" || !assignment.due_at)
    return 0;
  const due = Date.parse(assignment.due_at);
  if (!Number.isFinite(due) || now <= endOfDay(due)) return 0;
  return Math.max(1, Math.round((startOfDay(now) - startOfDay(due)) / DAY));
}

export const isOverdue = (a, now = Date.now()) => overdueDays(a, now) > 0;

/**
 * The Row view pill: `{text, tone, mine}`.
 * `Assigned to Meera Iyer · due Fri 24 Oct` · `Overdue · 2 days` ·
 * `Assigned to you by Meera Iyer · due …` · `Done · Meera Iyer`.
 */
export function pillOf(assignment, meId, now = Date.now()) {
  if (!assignment) return null;
  const mine = assignment.assignee?.id === meId;
  const due = assignment.due_at
    ? ` · due ${dueLabel(assignment.due_at, now)}`
    : "";
  if (assignment.status === "done")
    return {
      text: `Done · ${mine ? "you" : assignment.assignee?.name ?? "someone"}`,
      tone: "default",
      mine,
    };
  const late = overdueDays(assignment, now);
  const who = mine
    ? `Assigned to you${
        assignment.assigned_by?.id && assignment.assigned_by.id !== meId
          ? ` by ${assignment.assigned_by.name}`
          : ""
      }`
    : `Assigned to ${assignment.assignee?.name ?? "someone"}`;
  if (late)
    return {
      text: `${who} · Overdue · ${late} day${late === 1 ? "" : "s"}`,
      tone: "red",
      mine,
    };
  return { text: `${who}${due}`, tone: mine ? "amber" : "default", mine };
}

const nextWeekday = (from, weekday) => {
  const d = new Date(startOfDay(from));
  const add = (weekday - d.getDay() + 7) % 7;
  d.setDate(d.getDate() + add);
  return d;
};

/**
 * Quick picks: Today · Tomorrow · Friday · Next week (Monday), each
 * `{key, label, iso}` at the end of that local day. Friday is this week's,
 * or next week's when today is Friday or later.
 */
export function dueQuickPicks(now = Date.now()) {
  const today = new Date(startOfDay(now));
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  let friday = nextWeekday(now, 5);
  if (friday.getTime() <= today.getTime()) {
    friday = new Date(friday);
    friday.setDate(friday.getDate() + 7);
  }
  const monday = nextWeekday(tomorrow.getTime(), 1);
  const iso = (d) => new Date(endOfDay(d.getTime())).toISOString();
  return [
    { key: "today", label: "Today", iso: iso(today) },
    { key: "tomorrow", label: "Tomorrow", iso: iso(tomorrow) },
    { key: "friday", label: "Friday", iso: iso(friday) },
    { key: "next_week", label: "Next week", iso: iso(monday) },
  ];
}

/** `2026-10-24` from an ISO time (the date input's value, local day). */
export const dateInputValue = (iso) => {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return "";
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

/** End of the local day of a `YYYY-MM-DD` value, as ISO (or null). */
export const dueFromDateInput = (value) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? "");
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return new Date(endOfDay(d.getTime())).toISOString();
};

/**
 * `2 of these rows are assigned to Asha Rao; they move to Meera Iyer.`
 * (one sentence per current assignee, people other than the new one).
 */
export function moveLines(
  rowIds,
  assignments,
  assigneeName,
  assigneeId,
  meId = null,
) {
  const open = (assignments ?? []).filter(
    (a) => a.status === "open" && (rowIds ?? []).includes(a.row_id),
  );
  const byPerson = new Map();
  open.forEach((a) => {
    if (a.assignee?.id === assigneeId) return;
    const k = a.assignee?.id ?? "";
    if (!byPerson.has(k))
      byPerson.set(k, {
        name: meId && a.assignee?.id === meId ? "you" : a.assignee?.name,
        n: 0,
      });
    byPerson.get(k).n += 1;
  });
  const total = (rowIds ?? []).length;
  return [...byPerson.values()].map(({ name, n }) => {
    const subject =
      total === 1
        ? "This row is"
        : n === total
          ? `All ${total} rows are`
          : `${n} of these rows ${n === 1 ? "is" : "are"}`;
    const pronoun = n === 1 ? "it moves" : "they move";
    return `${subject} assigned to ${name}; ${pronoun} to ${assigneeName ?? "the new assignee"}.`;
  });
}

/** `Assign 5 rows` · `Assign 1 row` */
export const assignTitle = (n) =>
  `Assign ${n.toLocaleString("en-US")} row${n === 1 ? "" : "s"}`;

/** `Assigned 5 rows to Meera Iyer · due Fri 24 Oct` */
export function assignedToast(n, assigneeName, dueAt, now = Date.now()) {
  const due = dueAt ? ` · due ${dueLabel(dueAt, now)}` : "";
  return `Assigned ${n.toLocaleString("en-US")} row${n === 1 ? "" : "s"} to ${assigneeName}${due}`;
}

/**
 * Footer text: `5 assigned to you · 1 overdue` (`` when none);
 * `6 assigned to you on 5 rows` when a row also has a result row assigned
 * (`rows` = the server's `open_rows`, what the view it opens shows).
 */
export function footerAssignedText(counts, rows) {
  const open = counts?.open ?? 0;
  if (!open) return "";
  const late = counts?.overdue ?? 0;
  const on =
    Number.isFinite(rows) && rows !== open
      ? ` on ${rows.toLocaleString("en-US")} row${rows === 1 ? "" : "s"}`
      : "";
  return `${open.toLocaleString("en-US")} assigned to you${on}${late ? ` · ${late.toLocaleString("en-US")} overdue` : ""}`;
}

/** The open assignment of one row (and run), if any. */
export const openAssignmentOf = (assignments, rowId, runId = null) =>
  (assignments ?? []).find(
    (a) =>
      a.row_id === rowId &&
      (a.run_id ?? null) === (runId ?? null) &&
      a.status === "open",
  ) ?? null;

/**
 * v2.1 g8 (assign:results-selection): the row's open assignment made on one
 * of its result rows (`run_id` set), newest first, if any.
 */
export const openAssignmentOnAnyRun = (assignments, rowId) =>
  [...(assignments ?? [])]
    .filter((a) => a.row_id === rowId && a.run_id && a.status === "open")
    .sort((a, b) =>
      String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")),
    )[0] ?? null;
