// W4-1: row comments, pure. Shared by the comment UI and the mock
// server (which reads mentions from ids, never from names).
//
// Body format: Markdown with mention tokens `@[Meera Iyer](user:<id>)`.

const TOKEN_RE = /@\[([^\]\n]{1,120})\]\(user:([^)\s]{1,80})\)/g;

/** The token stored in a body for one person. */
export const mentionToken = ({ id, name }) => `@[${name}](user:${id})`;

/** Ids mentioned in a body, in order, without repeats. */
export function mentionIdsOf(body) {
  const ids = [];
  String(body ?? "").replace(TOKEN_RE, (_, __, id) => {
    if (!ids.includes(id)) ids.push(id);
    return "";
  });
  return ids;
}

/** The body as people read it: tokens become `@Name`. */
export const plainTextOf = (body) =>
  String(body ?? "").replace(TOKEN_RE, (_, name) => `@${name}`);

/**
 * The body split for rendering: `[{kind: "text", text} | {kind: "mention",
 * id, name}]`. Text parts are Markdown.
 */
export function bodyParts(body) {
  const parts = [];
  const s = String(body ?? "");
  let last = 0;
  s.replace(TOKEN_RE, (m, name, id, at) => {
    if (at > last) parts.push({ kind: "text", text: s.slice(last, at) });
    parts.push({ kind: "mention", id, name });
    last = at + m.length;
    return m;
  });
  if (last < s.length) parts.push({ kind: "text", text: s.slice(last) });
  return parts;
}

/** The body as Markdown with mentions in bold (`**@Meera Iyer**`). */
export const markdownOf = (body) =>
  String(body ?? "").replace(TOKEN_RE, (_, name) => `**@${name}**`);

/** `on expected_reply` · `on #4A · step 3 lookup_order` · `` (whole row). */
export function anchorLabel(anchor, { runLabel } = {}) {
  const parts = [];
  if (runLabel) parts.push(runLabel);
  if (anchor?.kind === "field" && anchor.column_name)
    parts.push(anchor.column_name);
  if (anchor?.kind === "step" && anchor.step_label)
    parts.push(`step ${anchor.step_label}`);
  return parts.length ? `on ${parts.join(" · ")}` : "";
}

/**
 * The composer's counter: `null` until the body reaches the server's
 * `warn_from`, then `{left, over}` (`120 characters left`).
 */
export function charCounter(body, limits) {
  const max = limits?.body_max;
  if (!Number.isFinite(max)) return null;
  const n = String(body ?? "").length;
  const from = Number.isFinite(limits?.warn_from) ? limits.warn_from : max;
  if (n < from) return null;
  const left = max - n;
  return {
    left,
    over: left < 0,
    text:
      left >= 0
        ? `${left.toLocaleString("en-US")} character${left === 1 ? "" : "s"} left`
        : `${(-left).toLocaleString("en-US")} over the limit`,
  };
}

/** What `me` may do on one comment (the server enforces the same rules). */
export function commentActions(
  comment,
  { meId, canEdit, isAdmin, mentionedInThread, canView = true },
) {
  if (!comment || comment.deleted)
    return {
      reply: false,
      react: false,
      resolve: false,
      edit: false,
      remove: false,
    };
  const own = comment.author?.id === meId;
  const root = !comment.parent_id;
  return {
    reply: Boolean(canView) && root,
    react: Boolean(canView),
    resolve: root && Boolean(canEdit || own || mentionedInThread),
    edit: own,
    remove: own || Boolean(isAdmin),
  };
}

/** Reactions in the server's order: `[{emoji, count, mine, names}]`. */
export function reactionList(
  reactions,
  allowed,
  { meId, nameOf = () => null } = {},
) {
  return (allowed ?? []).map((emoji) => {
    const ids = reactions?.[emoji] ?? [];
    return {
      emoji,
      count: ids.length,
      mine: ids.includes(meId),
      names: ids.map((id) => (id === meId ? "You" : nameOf(id) ?? "Someone")),
    };
  });
}

/** `Resolved by Asha Rao` (the fold line of a resolved thread). */
export const resolvedLine = (root, meId) =>
  root?.resolved
    ? `Resolved by ${root.resolved_by?.id === meId ? "you" : root.resolved_by?.name ?? "someone"}`
    : "";

/**
 * A folded resolved thread in one line: `Resolved by you · Asha Rao's
 * comment on expected_reply: “@Maya Chen #4A says…”` (`your comment` for
 * your own; no excerpt for a deleted root). `anchorText` = `anchorLabel`.
 */
export function foldLine(root, meId, anchorText = "") {
  if (!root?.resolved) return "";
  const whose =
    root.author?.id === meId
      ? "your comment"
      : root.author?.name
        ? `${root.author.name}'s comment`
        : "a comment";
  const excerpt = root.deleted
    ? ""
    : plainTextOf(root.body)
        .replace(/\*\*|`/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 140);
  return `${resolvedLine(root, meId)} · ${whose}${anchorText ? ` ${anchorText}` : ""}${excerpt ? `: “${excerpt}”` : ""}`;
}

const count = (n, one) =>
  `${n.toLocaleString("en-US")} ${one}${n === 1 ? "" : "s"}`;

/**
 * ` on 2 rows` when the rows differ from what was counted (two threads on
 * one row), so a footer number and the row view it opens never disagree.
 */
const onRows = (n, rows) =>
  Number.isFinite(rows) && rows !== n ? ` on ${count(rows, "row")}` : "";

/** `2 open comments` · `3 open comments on 2 rows` · `` */
export const openCommentsText = (n, rows) =>
  n > 0 ? `${count(n, "open comment")}${onRows(n, rows)}` : "";

/** `1 mention of you` · `2 mentions of you on 1 row` · `` */
export const mentionsText = (n, rows) =>
  n > 0 ? `${count(n, "mention")} of you${onRows(n, rows)}` : "";

/** Names of the grid views the footer opens: they count rows. */
export const OPEN_COMMENTS_VIEW = "Rows with open comments";
export const MENTIONS_VIEW = "Rows that mention you";

/** Threads filtered by the Comments sub-toggle (`open` · `resolved` · `all`). */
export function filterThreads(threads, status) {
  const list = threads ?? [];
  if (status === "open")
    return list.filter((t) => !t.root.deleted && !t.root.resolved);
  if (status === "resolved") return list.filter((t) => t.root.resolved);
  return list;
}

/**
 * `run_id` that reads every thread of a row in one list: the row's own and
 * each result row's (Activity › Comments and the Row header pill).
 */
export const ALL_RUN_THREADS = "all";

/**
 * The Open view's line when nothing is open: `null` without threads, else
 * whether everything is resolved or the rest (deleted roots that still
 * have replies) is under All.
 */
export function noOpenText(threads) {
  const list = threads ?? [];
  if (!list.length) return null;
  return list.every((t) => t.root.resolved)
    ? "Every thread here is resolved."
    : "No open threads. All shows the rest.";
}

const NOT_A_FIELD = new Set(["output", "eval", "human"]);

/**
 * "On [Whole row ▾]" choices: the whole row, then each user field of the
 * row by column name (generated outputs and evals excluded), in order.
 */
export function fieldAnchorOptions(columns) {
  const fields = (columns ?? []).filter(
    (c) =>
      c?.id &&
      c?.name &&
      !NOT_A_FIELD.has(c.role) &&
      !["evaluation", "run_prompt", "annotation_label", "experiment"].includes(
        c.origin_type,
      ),
  );
  return [
    { key: "row", label: "Whole row", anchor: { kind: "row" } },
    ...fields.map((c) => ({
      key: `field:${c.id}`,
      label: c.name,
      anchor: { kind: "field", column_id: c.id, column_name: c.name },
    })),
  ];
}

/**
 * The steps of a trace (TraceDetailResponse `observation_spans`, a tree of
 * `{observation_span, children}`), depth first, without the root span when
 * it has children: `[{span_id, name}]`.
 */
export function traceSteps(observationSpans) {
  const out = [];
  const walk = (nodes) =>
    (nodes ?? []).forEach((n) => {
      const s = n?.observation_span ?? n;
      if (s?.id) out.push({ span_id: s.id, name: s.name ?? null });
      walk(n?.children);
    });
  (observationSpans ?? []).forEach((root) => {
    if (root?.children?.length) walk(root.children);
    else walk([root]);
  });
  return out;
}

/**
 * "On step [▾]" choices from a try's trace steps `[{span_id, name, …}]`
 * (`trace_id` names the trace): `3 lookup_order`.
 */
export function stepAnchorOptions(spans, traceId) {
  return (spans ?? [])
    .filter((s) => s?.span_id || s?.id)
    .map((s, i) => {
      const id = s.span_id ?? s.id;
      const label = `${i + 1} ${s.name ?? s.span_name ?? "step"}`;
      return {
        key: `step:${id}`,
        label,
        anchor: {
          kind: "step",
          trace_id: traceId ?? null,
          span_id: id,
          step_label: label,
        },
      };
    });
}

/**
 * The Row view deep link that opens a row's comments with the caret in the
 * composer (RowCollabPills' "Comment on this row"; the grid's cell menu,
 * W4 §9 M6). `rcompose` is the one-shot token the Activity tab clears.
 */
export function commentDeepLink(
  params,
  rowId,
  now = Date.now(),
  { compose = true } = {},
) {
  const next = new URLSearchParams(params);
  if (rowId != null) next.set("row", String(rowId));
  next.set("rowTab", "activity");
  next.set("ract", "comments");
  next.set("rcs", "open");
  if (compose) next.set("rcompose", String(now));
  return next;
}
