// TH-150: in-memory store for the rich half of a prompt-editor copy/cut.
//
// Holds at most one record (latest copy/cut wins) in module memory, so it is
// per tab by construction and dies on reload. The OS clipboard only carries an
// opaque handle; everything else stays here and is re-validated on paste
// against the live application state, never against clipboard bytes.
import {
  ALL_MEDIA_KINDS,
  OMISSION_REASONS,
  RECORD_VERSION,
  TAB_ID,
} from "./constants";

let current = null;

export function put(record) {
  current = record;
}

export function get(handle) {
  return current && handle && current.handle === handle ? current : null;
}

export function clear() {
  current = null;
}

export function peek() {
  return current;
}

const ITEM_KEYS = {
  text: ["kind", "text"],
  variableClose: ["kind"],
  image: ["kind", "url", "name", "size"],
  audio: ["kind", "url", "name", "size", "mimeType"],
  pdf: ["kind", "url", "name", "size"],
};

function validItem(item) {
  if (!item || typeof item !== "object") return false;
  const allowed = ITEM_KEYS[item.kind];
  if (!allowed) return false;
  for (const key of Object.keys(item)) {
    if (!allowed.includes(key)) return false;
    if (typeof item[key] === "function") return false;
  }
  if (item.kind === "text") return typeof item.text === "string";
  if (item.kind === "variableClose") return true;
  if (!ALL_MEDIA_KINDS.includes(item.kind)) return false;
  if (typeof item.url !== "string" || item.url.length === 0) return false;
  if (item.name !== undefined && typeof item.name !== "string") return false;
  if (item.size !== undefined && typeof item.size !== "number") return false;
  if (item.mimeType !== undefined && typeof item.mimeType !== "string")
    return false;
  return true;
}

function sameProvenance(recorded, live) {
  if (!recorded || !live) return false;
  if (!live.userId || !live.orgId) return false; // logged out: reject everything
  if (recorded.tabId !== TAB_ID) return false;
  if (recorded.userId !== live.userId) return false;
  if (recorded.orgId !== live.orgId) return false;
  if (
    recorded.workspaceId != null &&
    live.workspaceId != null &&
    recorded.workspaceId !== live.workspaceId
  ) {
    return false;
  }
  return true;
}

/**
 * Returns `{ ok: true }` or `{ ok: false, reason }` without throwing.
 * `clipboardText` is the text/plain the OS clipboard currently holds; a
 * mismatch means the clipboard moved on since the record was written.
 */
export function validateRecord(record, { clipboardText, live }) {
  if (!record || typeof record !== "object") {
    return { ok: false, reason: OMISSION_REASONS.OTHER_CONTEXT };
  }
  if (record.version !== RECORD_VERSION) {
    return { ok: false, reason: OMISSION_REASONS.INVALID };
  }
  if (typeof record.text !== "string" || record.text !== clipboardText) {
    return { ok: false, reason: OMISSION_REASONS.OTHER_CONTEXT };
  }
  if (!sameProvenance(record.provenance, live)) {
    return { ok: false, reason: OMISSION_REASONS.OTHER_CONTEXT };
  }
  if (!Array.isArray(record.items) || !record.items.every(validItem)) {
    return { ok: false, reason: OMISSION_REASONS.INVALID };
  }
  return { ok: true };
}
