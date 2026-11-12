// Pure helpers for the Versions UI (PRODUCT-SPEC §3.5): change counts copy,
// History grouping, the Save dialog's change list and auto description, and
// tag rules. No React, no I/O.

export const TAG_PATTERN = /^[a-z0-9][a-z0-9._-]{0,29}$/;

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * "2 added · 5 edited · 1 deleted · 3 split changes" for change-set counts
 * (changeSetStore `countOps`). Zero parts are left out; `deleted: null`
 * (a whole-selection delete) reads "matching rows deleted".
 */
export function pendingCountsText(counts) {
  if (!counts) return "";
  const parts = [];
  if (counts.added) parts.push(`${counts.added} added`);
  if (counts.edited) parts.push(`${counts.edited} edited`);
  if (counts.deleted === null) parts.push("matching rows deleted");
  else if (counts.deleted) parts.push(`${counts.deleted} deleted`);
  if (counts.split_changes)
    parts.push(plural(counts.split_changes, "split change"));
  if (counts.status_changes)
    parts.push(plural(counts.status_changes, "status change"));
  if (counts.tag_changes) parts.push(plural(counts.tag_changes, "tag change"));
  if (counts.environment)
    parts.push(
      environmentChangeText(counts.environment.from, counts.environment.to),
    );
  return parts.join(" · ");
}

/** `ghcr.io/acme/acme-support-sandbox:1.4` → `acme-support-sandbox:1.4`. */
export function shortImage(image) {
  const s = String(image ?? "").trim();
  return s ? s.split("/").pop() : "—";
}

/**
 * `Environment acme-support-sandbox:1.4 → 1.5` (same repository: the new
 * tag only), else both short images.
 */
export function environmentChangeText(fromImage, toImage) {
  const from = shortImage(fromImage);
  const to = shortImage(toImage);
  const [repoA, tagA] = from.split(":");
  const [repoB, tagB] = to.split(":");
  const right = repoA === repoB && tagA != null && tagB != null ? tagB : to;
  return `Environment ${from} → ${right}`;
}

/** Number of pending changes for "You have 3 unsaved changes." */
export function pendingTotal(counts) {
  if (!counts) return 0;
  return (
    (counts.added ?? 0) +
    (counts.edited ?? 0) +
    (counts.deleted ?? 1) +
    (counts.split_changes ?? 0) +
    (counts.environment ? 1 : 0)
  );
}

const DAY_MS = 24 * 60 * 60 * 1000;
const startOfDay = (ms) => {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
};

/** "Today" | "Yesterday" | "This week" | "Older" for a timestamp. */
export function dayBucket(iso, now = Date.now()) {
  const at = new Date(iso).getTime();
  if (!Number.isFinite(at)) return "Older";
  const today = startOfDay(now);
  if (at >= today) return "Today";
  if (at >= today - DAY_MS) return "Yesterday";
  if (at >= today - 6 * DAY_MS) return "This week";
  return "Older";
}

export const BUCKETS = ["Today", "Yesterday", "This week", "Older"];

/** N1 versions (newest first) grouped into the History buckets, in order. */
export function groupVersions(versions, now = Date.now()) {
  const groups = new Map(BUCKETS.map((b) => [b, []]));
  (versions ?? []).forEach((v) =>
    groups.get(dayBucket(v.created_at, now)).push(v),
  );
  return BUCKETS.map((label) => ({
    label,
    versions: groups.get(label),
  })).filter((g) => g.versions.length);
}

/** `+10 ~2 −0` parts of a version's counts. */
export const countsParts = (counts) => ({
  added: counts?.added ?? 0,
  modified: counts?.modified ?? 0,
  deleted: counts?.deleted ?? 0,
  splitChanges: counts?.split_changes ?? 0,
});

/**
 * The Save dialog's change list: ops grouped by row, in first-touched order.
 * Each group: `{rowId, isNew, items[]}`; items are
 *   {kind:"added", opId, cells, splitIds}
 *   {kind:"cell", opId, columnId, before, after}
 *   {kind:"metadata", opId, before, after}
 *   {kind:"splits", opId, add[], remove[]}
 *   {kind:"deleted", opId}
 * A whole-selection delete is returned separately as `selectionDelete`.
 */
export function groupOpsByRow(ops) {
  const groups = new Map();
  const group = (rowId, isNew = false) => {
    if (!groups.has(rowId)) groups.set(rowId, { rowId, isNew, items: [] });
    const g = groups.get(rowId);
    if (isNew) g.isNew = true;
    return g;
  };
  let selectionDelete = null;
  (ops ?? []).forEach((op) => {
    switch (op.op) {
      case "add_row":
        group(op.temp_id, true).items.push({
          kind: "added",
          opId: op.id,
          cells: op.cells ?? {},
          splitIds: op.split_ids ?? [],
        });
        break;
      case "update_cell":
        group(op.row_id).items.push({
          kind: "cell",
          opId: op.id,
          columnId: op.column_id,
          before: op.before,
          after: op.value,
        });
        break;
      case "set_row_metadata":
        group(op.row_id).items.push({
          kind: "metadata",
          opId: op.id,
          before: op.before,
          after: op.metadata,
        });
        break;
      case "set_splits":
        (op.row_ids ?? []).forEach((rowId) =>
          group(rowId).items.push({
            kind: "splits",
            opId: op.id,
            add: op.add ?? [],
            remove: op.remove ?? [],
          }),
        );
        break;
      case "set_status":
        (op.row_ids ?? []).forEach((rowId) =>
          group(rowId).items.push({
            kind: "status",
            opId: op.id,
            status: op.status,
            reason: op.reason ?? null,
          }),
        );
        break;
      case "set_tags":
        (op.row_ids ?? []).forEach((rowId) =>
          group(rowId).items.push({
            kind: "tags",
            opId: op.id,
            add: op.add ?? [],
            remove: op.remove ?? [],
          }),
        );
        break;
      case "delete_rows":
        if (op.selection)
          selectionDelete = { opId: op.id, selection: op.selection };
        else
          (op.row_ids ?? []).forEach((rowId) =>
            group(rowId).items.push({ kind: "deleted", opId: op.id }),
          );
        break;
      default:
        break;
    }
  });
  return { rows: [...groups.values()], selectionDelete };
}

const isEmpty = (v) => v == null || v === "" || v === "[]" || v === "{}";

/**
 * Auto description for a change-set, saved when the field is left empty.
 * `names` resolves labels: {rowLabel(id) → "Row 241", columnName(id),
 * splitName(id)}.
 *   one cell        "Filled expected_reply on Row 241" / "Edited … on Row 12"
 *   splits only     "Added 71 rows to split hard"
 *   mixed           "Edited 5 rows, added 2, deleted 1; 3 split changes"
 */
export function describeChangeSet(ops, names = {}) {
  // Imported rows (Add-data sheet, uploads) carry their own description:
  // used when every op came from the same import.
  const hints = new Set((ops ?? []).map((o) => o.description_hint));
  if (
    ops?.length &&
    ops.every((o) => o.description_hint) &&
    hints.size === 1 &&
    [...hints][0]
  )
    return [...hints][0];
  const rowLabel = names.rowLabel ?? ((id) => id);
  const columnName = names.columnName ?? ((id) => id);
  const splitName = names.splitName ?? ((id) => id);
  const list = ops ?? [];
  const cells = list.filter((o) => o.op === "update_cell");
  const meta = list.filter((o) => o.op === "set_row_metadata");
  const adds = list.filter((o) => o.op === "add_row");
  const splitOps = list.filter((o) => o.op === "set_splits");
  const deletes = list.filter((o) => o.op === "delete_rows");
  const deletedCount = deletes.reduce(
    (n, o) => n + (o.row_ids?.length ?? 0),
    0,
  );
  const hasSelectionDelete = deletes.some((o) => o.selection);
  const editedRows = new Set([...cells, ...meta].map((o) => o.row_id));

  const splitText = () => {
    if (!splitOps.length) return null;
    const added = new Map();
    const removed = new Map();
    splitOps.forEach((o) =>
      (o.row_ids ?? []).forEach((r) => {
        (o.add ?? []).forEach((s) =>
          added.set(s, (added.get(s) ?? new Set()).add(r)),
        );
        (o.remove ?? []).forEach((s) =>
          removed.set(s, (removed.get(s) ?? new Set()).add(r)),
        );
      }),
    );
    const rows = new Set(splitOps.flatMap((o) => o.row_ids ?? []));
    if (added.size === 1 && !removed.size) {
      const [[split, ids]] = [...added.entries()];
      return ids.size === 1
        ? `added ${rowLabel([...ids][0])} to split ${splitName(split)}`
        : `added ${ids.size} rows to split ${splitName(split)}`;
    }
    if (removed.size === 1 && !added.size) {
      const [[split, ids]] = [...removed.entries()];
      return ids.size === 1
        ? `removed ${rowLabel([...ids][0])} from split ${splitName(split)}`
        : `removed ${ids.size} rows from split ${splitName(split)}`;
    }
    return plural(rows.size, "split change");
  };

  const parts = [];
  if (cells.length === 1 && !meta.length && editedRows.size === 1) {
    const [c] = cells;
    parts.push(
      `${isEmpty(c.before) ? "Filled" : "Edited"} ${columnName(c.column_id)} on ${rowLabel(c.row_id)}`,
    );
  } else if (editedRows.size) {
    parts.push(`Edited ${plural(editedRows.size, "row")}`);
  }
  if (adds.length)
    parts.push(
      parts.length
        ? `added ${adds.length}`
        : `Added ${plural(adds.length, "row")}`,
    );
  if (deletedCount)
    parts.push(
      parts.length
        ? `deleted ${deletedCount}`
        : `Deleted ${plural(deletedCount, "row")}`,
    );
  if (hasSelectionDelete)
    parts.push(
      parts.length ? "deleted matching rows" : "Deleted matching rows",
    );
  const statusText = describeStatusOps(list, names);
  if (statusText)
    parts.push(parts.length ? lowerFirst(statusText) : statusText);
  const tagOps = list.filter((o) => o.op === "set_tags");
  if (tagOps.length) {
    const rows = new Set(tagOps.flatMap((o) => o.row_ids ?? []));
    const added = [...new Set(tagOps.flatMap((o) => o.add ?? []))];
    const removed = [...new Set(tagOps.flatMap((o) => o.remove ?? []))];
    const what = [
      added.length ? `tagged ${added.join(", ")}` : null,
      removed.length ? `untagged ${removed.join(", ")}` : null,
    ]
      .filter(Boolean)
      .join(" and ");
    const text = `${what.charAt(0).toUpperCase()}${what.slice(1)} on ${plural(rows.size, "row")}`;
    parts.push(parts.length ? lowerFirst(text) : text);
  }
  const env = list.find((o) => o.op === "set_environment");
  if (env) {
    const text = environmentChangeText(env.before?.image, env.image);
    parts.push(parts.length ? text.replace(/^E/, "e") : text);
  }
  const splits = splitText();
  if (!parts.length && splits)
    return splits.charAt(0).toUpperCase() + splits.slice(1);
  if (splits) return `${parts.join(", ")}; ${splits}`;
  return parts.join(", ") || "Edited rows";
}

const lowerFirst = (t) => t.charAt(0).toLowerCase() + t.slice(1);
const STATUS_VERB = {
  approved: "Approved",
  needs_review: "Marked for review",
  draft: "Marked as draft",
  archived: "Archived",
};

/**
 * W3-3 auto note for status ops: `Approved 4 rows (Maya Chen) · Archived 3
 * rows: Duplicates of Row 12`. `names.actorName` names the approver.
 */
export function describeStatusOps(ops, names = {}) {
  const byKey = new Map();
  (ops ?? [])
    .filter((o) => o.op === "set_status")
    .forEach((o) => {
      const key = `${o.status}|${o.status === "archived" ? o.reason ?? "" : ""}`;
      const cur = byKey.get(key) ?? {
        status: o.status,
        reason: o.reason ?? null,
        rows: new Set(),
      };
      (o.row_ids ?? []).forEach((id) => cur.rows.add(id));
      byKey.set(key, cur);
    });
  const restored = (ops ?? []).some((o) => o.op === "set_status" && o.restore);
  return [...byKey.values()]
    .map(({ status, reason, rows }) => {
      const verb =
        restored && status === "approved"
          ? "Restored"
          : STATUS_VERB[status] ?? "Set status on";
      const base = `${verb} ${plural(rows.size, "row")}`;
      if (status === "approved" && names.actorName && verb === "Approved")
        return `${base} (${names.actorName})`;
      if (status === "archived" && reason) return `${base}: ${reason}`;
      return base;
    })
    .join(" · ");
}

// W3-9: environments are labels that sit on one version at a time and move
// with a note (the server answers 400 `Add a note to move an environment`).
export const ENVIRONMENT_LABELS = Object.freeze(["production", "staging"]);
export const ENVIRONMENT_NOTE_REQUIRED = "Add a note to move an environment";

/** True when `tag` is an environment of this dataset (reserved or listed). */
export const isEnvironmentTag = (tag, versions = []) =>
  ENVIRONMENT_LABELS.includes(tag) ||
  versions.some((v) =>
    (v.labels ?? []).some((l) => l.name === tag && l.kind === "environment"),
  );

/**
 * `Maya Chen moved production v5 → v6 · Ship v6 after smoke passed`, or
 * `Maya Chen put production on v5 · Baseline for Oct release`.
 */
export function labelMoveText(m) {
  if (!m) return "";
  const who = m.actor ?? "Someone";
  const what =
    m.from != null
      ? `moved ${m.label} v${m.from} → v${m.to}`
      : `put ${m.label} on v${m.to}`;
  return `${who} ${what}${m.note ? ` · ${m.note}` : ""}`;
}

/** null when valid, else the message shown under the tag field. */
export function tagError(tag) {
  if (!tag) return null;
  if (!TAG_PATTERN.test(tag))
    return "Use lowercase letters, numbers, dots, dashes or underscores (max 30).";
  return null;
}

/**
 * Helper text under a tag field: "golden-v3 moves from v5 to v7" when the
 * tag already points at another version, "New tag" when unused, "Already on
 * v7" when it is on the target already.
 */
export function tagMoveText(tag, versions, targetNumber) {
  if (!tag) return null;
  const holder = (versions ?? []).find((v) => (v.tags ?? []).includes(tag));
  if (!holder) return `New tag · points at v${targetNumber}`;
  if (holder.number === targetNumber) return `Already on v${targetNumber}`;
  return `${tag} moves from v${holder.number} to v${targetNumber}`;
}

/** Every tag on a dataset's versions, newest version first. */
export const allTags = (versions) => [
  ...new Set((versions ?? []).flatMap((v) => v.tags ?? [])),
];

const parsedObject = (value) => {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string" || !/^\s*[[{]/.test(value)) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
};

/**
 * IT-R2 G5 P2: a conversation reads `Conversation · 8 turns · failed at
 * turn 2 (Resolution)`, never its JSON. null when `value` is not one.
 */
export function conversationSummary(value) {
  const v = parsedObject(value);
  const turns = Array.isArray(v)
    ? v
    : Array.isArray(v?.turns)
      ? v.turns
      : Array.isArray(v?.messages)
        ? v.messages
        : null;
  if (!turns?.length || !turns.every((t) => t && typeof t === "object"))
    return null;
  if (!turns.some((t) => "role" in t || "content" in t)) return null;
  const parts = [
    "Conversation",
    `${turns.length} ${turns.length === 1 ? "turn" : "turns"}`,
  ];
  const failing = v?.failing_turn ?? null;
  if (failing != null && String(v?.resolution ?? "").toLowerCase() === "fail")
    parts.push(`failed at turn ${failing} (Resolution)`);
  else if (failing != null) parts.push(`failing turn ${failing}`);
  return parts.join(" · ");
}

/** Short, single-line form of a value for a change row (not a diff). */
export function shortValue(value, max = 140) {
  if (value == null || value === "") return "empty";
  const conversation = conversationSummary(value);
  if (conversation) return conversation;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** "v3 → v6 · 26 added · 8 edited · 2 deleted" for an N4 result. */
export function changesTitle(result) {
  if (!result) return "";
  const c = result.counts ?? {};
  const parts = [
    `${c.added ?? 0} added`,
    `${c.modified ?? 0} edited`,
    `${c.deleted ?? 0} deleted`,
  ];
  if (c.split_changes) parts.push(plural(c.split_changes, "split change"));
  if (!result.from) return `v${result.to} · ${parts.join(" · ")}`;
  return `v${result.from} → v${result.to} · ${parts.join(" · ")}`;
}

/** "just now", "5m ago", "2h ago", "3d ago", then "Sep 2". */
export function compactAgo(iso, now = Date.now()) {
  const at = new Date(iso).getTime();
  if (!Number.isFinite(at)) return "";
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 45) return `${d}d ago`;
  return new Date(at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

// ---- R3 (P2) ----------------------------------------------------------

/**
 * RV#31: the non-zero parts of a version's `+ ~ −` counts, in order, with
 * their tooltip words: `[{key, glyph, n, word}]`. All zero → [].
 */
export function deltaParts(counts) {
  const c = countsParts(counts);
  // integration: rows restored with their ids count as added (N1); shown apart.
  const restored = Math.min(c.added, counts?.restored ?? 0);
  return [
    { key: "added", glyph: "+", n: c.added - restored, word: "added" },
    { key: "restored", glyph: "+", n: restored, word: "restored" },
    { key: "modified", glyph: "~", n: c.modified, word: "edited" },
    { key: "deleted", glyph: "−", n: c.deleted, word: "removed" },
  ].filter((p) => p.n > 0);
}

/** `+10 rows added, ~2 rows edited` (zero parts left out). */
export function deltaTooltip(counts) {
  const parts = deltaParts(counts).map((p) =>
    p.key === "restored"
      ? restoredRowsText(p.n, counts?.restored_from)
      : `${p.glyph}${plural(p.n, "row")} ${p.word}`,
  );
  const s = countsParts(counts).splitChanges;
  if (s) parts.push(plural(s, "split change"));
  return parts.length ? parts.join(", ") : "No row changes";
}

// ------------------------------------------------ integration · restore rows
/** `Restored 2 rows (from v4)` (the history entry's line for restore_rows). */
export function restoredRowsText(n, fromVersion) {
  return `Restored ${plural(n, "row")}${fromVersion ? ` (from v${fromVersion})` : ""}`;
}

/** `Row 31` · `Rows 11 and 31` · `Rows 11, 31 and 40` (ascending). */
export function rowNumbersText(numbers = []) {
  const ns = [...new Set(numbers.filter((x) => Number.isFinite(x)))].sort(
    (a, b) => a - b,
  );
  if (!ns.length) return "";
  if (ns.length === 1) return `Row ${ns[0]}`;
  return `Rows ${ns.slice(0, -1).join(", ")} and ${ns.at(-1)}`;
}

/**
 * The Restore rows dialog's words (N2 `restore_rows`): `rows` = the row
 * numbers coming back, `from` = the version they come from, `latest` the
 * latest number. `{title, body, confirm, description, toast}`.
 */
export function restoreRowsText({ rows = [], from, latest }) {
  const next = latest + 1;
  const n = rows.length;
  const names = rowNumbersText(rows);
  const one = n === 1;
  return {
    title: `Restore ${plural(n, "row")} from v${from} as v${next}?`,
    body: `${names} come${one ? "s" : ""} back as ${one ? "it was" : "they were"} in v${from}, with the same ${one ? "id and row number" : "ids and row numbers"}. No other row changes. v${latest} stays in the history.`,
    confirm: `Restore as v${next}`,
    description: `Restored ${names} from v${from}`,
    toast: (saved) => `Saved v${saved ?? next} · ${plural(n, "row")} restored`,
  };
}

/**
 * integration: Restore version's note when rows deleted after `version` come
 * back as new rows (N4 counts of `version → latest`: `deleted` > 0), or null.
 */
export function restoreAsNewRowsText(counts, version) {
  if (!(countsParts(counts).deleted > 0)) return null;
  return `Rows deleted after v${version} come back as new rows with new numbers. To keep a row's id, open v${version} and use Restore row.`;
}

/** The sentence a pending change-set blocks a restore with (both dialogs). */
export const RESTORE_BLOCKED_PENDING =
  "Save or discard your unsaved changes first: a restore starts from the latest saved version.";

/**
 * integration: the restore-rows request behind an as-of selection (grid rows with
 * `row_meta.change`): rows deleted later (`deleted_later`, compare=since)
 * come back from the viewed version; rows this version deleted (`deleted`,
 * compare=in) from its parent. `{rows:[{row_id, n}], from}` or null.
 */
export function restorableRows(rows = [], asOfNumber) {
  if (!Number.isFinite(asOfNumber)) return null;
  const later = [];
  const inThis = [];
  rows.forEach((r) => {
    const kind = r?.row_meta?.change?.kind;
    // A row in the latest version again (restored since) is not restorable.
    if (r?.row_meta?.change?.in_latest) return;
    const entry = {
      row_id: r.row_id,
      n: Number.isFinite(r.order) ? r.order + 1 : r.n ?? null,
    };
    if (kind === "deleted_later") later.push(entry);
    else if (kind === "deleted") inThis.push(entry);
  });
  if (later.length) return { rows: later, from: asOfNumber };
  if (inThis.length && asOfNumber > 1)
    return { rows: inThis, from: asOfNumber - 1 };
  return null;
}

/**
 * RV#30: what `Restore as vN` does against the latest version, from the
 * N4 counts of `version → latest` (and its `status_rows`, third argument).
 */
export function restoreItems(counts, _version, statusRows = []) {
  const c = countsParts(counts);
  const out = [];
  // W3-3: `statusRows` (N4 `status_rows`) are the rows whose status differs.
  // W3-3: `before` is the status in the restored version, `after` today's.
  const unarchive = statusRows.filter(
    (r) => r.after === "archived" && r.before !== "archived",
  ).length;
  const rearchive = statusRows.filter(
    (r) => r.before === "archived" && r.after !== "archived",
  ).length;
  const otherStatus = statusRows.length - unarchive - rearchive;
  if (c.added)
    out.push(`${plural(c.added, "row")} added after it will be removed`);
  if (c.modified)
    out.push(
      `${plural(c.modified, "edited row")} go${c.modified === 1 ? "es" : ""} back to ${c.modified === 1 ? "its" : "their"} old values`,
    );
  if (c.deleted)
    out.push(
      `${plural(c.deleted, "deleted row")} come${c.deleted === 1 ? "s" : ""} back`,
    );
  if (c.splitChanges)
    out.push(`${plural(c.splitChanges, "row")} get their old splits back`);
  if (unarchive)
    out.push(
      `${plural(unarchive, "archived row")} come${unarchive === 1 ? "s" : ""} back`,
    );
  if (rearchive)
    out.push(
      `${plural(rearchive, "row")} ${rearchive === 1 ? "is" : "are"} archived again`,
    );
  if (otherStatus)
    out.push(
      `${plural(otherStatus, "row")} get${otherStatus === 1 ? "s its" : " their"} old status back`,
    );
  return out;
}

/**
 * RV#32: versions kept by the history filter — text (number, tag,
 * description, author) and the chips `tagged`, `mine` (by user id or
 * name), `used` (ran in an experiment).
 */
export function filterVersions(versions = [], { q = "", chips = [], me } = {}) {
  const t = q
    .trim()
    .toLowerCase()
    .replace(/^v(?=\d)/, "");
  return versions.filter((v) => {
    if (chips.includes("tagged") && !(v.tags ?? []).length) return false;
    if (chips.includes("used") && !(v.experiments ?? []).length) return false;
    if (
      chips.includes("mine") &&
      !(
        (me?.id && v.created_by?.id === me.id) ||
        (me?.name && v.created_by?.name === me.name)
      )
    )
      return false;
    if (!t) return true;
    return [
      String(v.number),
      ...(v.tags ?? []),
      v.description ?? "",
      v.created_by?.name ?? "",
    ].some((x) => String(x).toLowerCase().includes(t));
  });
}
