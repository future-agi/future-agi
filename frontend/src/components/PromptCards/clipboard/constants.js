// TH-150: constants for the prompt editor clipboard path.

// Custom DataTransfer type carrying an opaque handle into the in-memory store.
// The handle confers nothing by itself: no URL, name, size or credential ever
// reaches the OS clipboard through this type.
export const INTERNAL_MIME = "application/x-futureagi-prompt-ref";

export const RECORD_VERSION = 1;

// Generated once per page load: a record from another tab or from before a
// reload never matches.
export const TAB_ID =
  globalThis.crypto?.randomUUID?.() ??
  `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export const ALL_MEDIA_KINDS = ["image", "audio", "pdf"];
// Default for PromptEditor: no attachment kind may be inserted by paste.
export const NO_MEDIA_KINDS = Object.freeze([]);

export const OMISSION_REASONS = {
  UNSUPPORTED: "unsupported", // clipboard unavailable / write failed
  OTHER_CONTEXT: "other_context", // other tab, reload, logout, other organization
  NOT_ALLOWED: "not_allowed", // destination does not accept this media type
  INVALID: "invalid", // malformed or unknown payload
};

export const OMISSION_MESSAGES = {
  [OMISSION_REASONS.UNSUPPORTED]:
    "Clipboard unavailable: nothing was copied or removed",
  [OMISSION_REASONS.OTHER_CONTEXT]:
    "Attachments were not pasted: copy and paste them in the same tab and organization",
  [OMISSION_REASONS.NOT_ALLOWED]:
    "Attachments were not pasted: this field does not accept that media type",
  [OMISSION_REASONS.INVALID]: "Attachments were not pasted",
};
