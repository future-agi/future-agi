// Reviewer note drafts in this browser (KH-SCOPE §6 B2, owner decision 4):
// pure storage and rules behind useNoteDraft. A draft is the reviewer's
// unsubmitted item note and per-label notes for one item:
//   localStorage["fa.review.note.<queueId>.<itemId>.<userId>"] =
//     {itemNotes, labelNotes: {<label id>: text}, savedAt: epoch ms}
// Every storage access is wrapped: with storage off the page works and the
// status says the draft is not kept. Retention, debounce and eviction follow
// Tasks' useTaskDraft.js (sections/tasks/hooks/useTaskDraft.js), which is
// URL-bound, so it is not reused.

export const NOTE_DRAFT_PREFIX = "fa.review.note.";
// Writes coalesce while typing: useTaskDraft.js debounces its writes 300 ms.
export const NOTE_DRAFT_DEBOUNCE_MS = 300;
// Drafts older than this are dropped: useTaskDraft.js TTL_MS (7 days).
export const NOTE_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
// The status line's age reads in minutes, so it refreshes on a half-minute
// beat (never more than 30 s late).
export const NOTE_DRAFT_TICK_MS = 30 * 1000;

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

const storage = () => {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
};

/** The draft key, or null while any part is unknown. */
export function noteDraftKey(queueId, itemId, userId) {
  if (!queueId || !itemId || !userId) return null;
  return `${NOTE_DRAFT_PREFIX}${queueId}.${itemId}.${userId}`;
}

/** Label notes without the empty ones ({a: ""} reads as {}). */
export function cleanLabelNotes(labelNotes) {
  const out = {};
  Object.entries(labelNotes ?? {}).forEach(([id, text]) => {
    if (typeof text === "string" && text !== "") out[id] = text;
  });
  return out;
}

/** {itemNotes, labelNotes} in one comparable form. */
export function normalizeNotes(notes) {
  return {
    itemNotes: typeof notes?.itemNotes === "string" ? notes.itemNotes : "",
    labelNotes: cleanLabelNotes(notes?.labelNotes),
  };
}

const sortedEntries = (obj) =>
  Object.entries(obj).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));

/** A string that changes exactly when the notes' text changes. */
export function notesSignature(notes) {
  const n = normalizeNotes(notes);
  return JSON.stringify([n.itemNotes, sortedEntries(n.labelNotes)]);
}

export const sameNotes = (a, b) => notesSignature(a) === notesSignature(b);

const hasAnyNote = (notes) => {
  const n = normalizeNotes(notes);
  return n.itemNotes !== "" || Object.keys(n.labelNotes).length > 0;
};

const toMs = (t) => {
  if (t == null || t === "") return null;
  const ms = typeof t === "number" ? t : Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
};

/**
 * What the server holds for this reviewer and item, read from the annotate
 * detail: the item's `existing_notes`, the reviewer's own scores' `notes`
 * and the latest of those scores' `updated_at` (the submit writes both).
 */
export function serverNotesOf({ annotations = [], itemNotes = "" } = {}) {
  const labelNotes = {};
  let savedAt = null;
  (annotations ?? []).forEach((a) => {
    if (a?.label_id && typeof a.notes === "string" && a.notes !== "")
      labelNotes[a.label_id] = a.notes;
    const at = toMs(a?.updated_at ?? a?.created_at);
    if (at != null && (savedAt == null || at > savedAt)) savedAt = at;
  });
  return { itemNotes: itemNotes ?? "", labelNotes, savedAt };
}

/**
 * Restore a draft when it differs from the server's notes and the server has
 * none, or its notes are older than the draft (or of unknown age).
 */
export function shouldRestore(draft, server) {
  if (!draft) return false;
  if (sameNotes(draft, server)) return false;
  if (!hasAnyNote(server)) return true;
  const serverAt = toMs(server?.savedAt);
  return serverAt == null || draft.savedAt > serverAt;
}

/** The stored draft, or null (missing, malformed or expired: removed). */
export function readNoteDraft(key, now = Date.now()) {
  const ls = storage();
  if (!key || !ls) return null;
  try {
    const raw = ls.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      !parsed ||
      typeof parsed !== "object" ||
      typeof parsed.savedAt !== "number"
    ) {
      ls.removeItem(key);
      return null;
    }
    if (now - parsed.savedAt > NOTE_DRAFT_TTL_MS) {
      ls.removeItem(key);
      return null;
    }
    return { ...normalizeNotes(parsed), savedAt: parsed.savedAt };
  } catch {
    return null;
  }
}

/** Store a draft; true when the browser kept it. */
export function writeNoteDraft(key, notes, now = Date.now()) {
  const ls = storage();
  if (!key || !ls) return false;
  try {
    ls.setItem(key, JSON.stringify({ ...normalizeNotes(notes), savedAt: now }));
    return true;
  } catch {
    return false;
  }
}

export function removeNoteDraft(key) {
  const ls = storage();
  if (!key || !ls) return;
  try {
    ls.removeItem(key);
  } catch {
    /* storage off: nothing was kept */
  }
}

/** Drop every note draft older than the TTL (as useTaskDraft does). */
export function evictExpiredNoteDrafts(now = Date.now()) {
  const ls = storage();
  if (!ls) return;
  try {
    for (let i = ls.length - 1; i >= 0; i -= 1) {
      const key = ls.key(i);
      if (!key || !key.startsWith(NOTE_DRAFT_PREFIX)) continue;
      try {
        const parsed = JSON.parse(ls.getItem(key) ?? "null");
        if (
          !parsed ||
          typeof parsed.savedAt !== "number" ||
          now - parsed.savedAt > NOTE_DRAFT_TTL_MS
        )
          ls.removeItem(key);
      } catch {
        ls.removeItem(key);
      }
    }
  } catch {
    /* storage off */
  }
}

/** "just now", "2 min ago", "3 h ago", "2 days ago". */
export function draftAgeText(savedAt, now = Date.now()) {
  const age = Math.max(0, now - savedAt);
  if (age < MINUTE_MS) return "just now";
  if (age < HOUR_MS) return `${Math.floor(age / MINUTE_MS)} min ago`;
  if (age < DAY_MS) return `${Math.floor(age / HOUR_MS)} h ago`;
  const days = Math.floor(age / DAY_MS);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

/** The status line's words for a draft state (null = show nothing). */
export function draftStatusText({ status, savedAt } = {}, now = Date.now()) {
  if (status === "restored") return "Restored your unsaved note";
  if (status === "saved" && savedAt != null)
    return `Draft saved · ${draftAgeText(savedAt, now)}`;
  if (status === "unavailable")
    return "Draft not kept: this browser blocks storage. Submit to save the note.";
  return null;
}
