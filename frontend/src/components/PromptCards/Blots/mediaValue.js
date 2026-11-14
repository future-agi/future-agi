// The blots' static create(value) historically expected the flat shape that
// PromptEditor builds ({ url, name, size, mimeType, id, ...callbacks }), while
// their static value(node) returns the nested shape stored on the DOM
// ({ id, imageData | audioData | pdfData }). Quill's history module replays the
// value() shape on undo/redo and the clipboard module replays it on paste, so
// create() has to understand both or the restored card has no URL.

export const MEDIA_BLOT_NAMES = ["ImageBlot", "AudioBlot", "PdfBlot"];

export const MEDIA_KIND_BY_BLOT = {
  ImageBlot: "image",
  AudioBlot: "audio",
  PdfBlot: "pdf",
};

export const BLOT_BY_MEDIA_KIND = {
  image: "ImageBlot",
  audio: "AudioBlot",
  pdf: "PdfBlot",
};

const NESTED_KEY = { image: "imageData", audio: "audioData", pdf: "pdfData" };

const NESTED_FIELDS = {
  image: { name: "img_name", size: "img_size" },
  audio: { name: "audio_name", size: "audio_size", mimeType: "audio_type" },
  pdf: { name: "pdf_name", size: "pdf_size" },
};

/**
 * Accepts the flat create() shape or the nested value() shape and returns
 * `{ id, url, name, size, mimeType, callbacks }`. `callbacks` holds any
 * function-valued keys that were present on the input (handleRemoveImage,
 * setSelectedImage, ...). A missing id gets a fresh one.
 */
export function normalizeMediaValue(kind, value, makeId) {
  const v = value && typeof value === "object" ? value : {};
  const nested = v[NESTED_KEY[kind]];
  const fields = NESTED_FIELDS[kind];
  const callbacks = {};
  Object.keys(v).forEach((key) => {
    if (typeof v[key] === "function") callbacks[key] = v[key];
  });

  let url;
  let name;
  let size;
  let mimeType;
  if (nested && typeof nested === "object") {
    url = nested.url;
    name = nested[fields.name];
    size = nested[fields.size];
    mimeType = fields.mimeType ? nested[fields.mimeType] : undefined;
  } else {
    url = v.url;
    name = v.name;
    size = v.size;
    mimeType = v.mimeType;
  }

  return {
    id: v.id || (makeId ? makeId() : undefined),
    url,
    name,
    size,
    mimeType,
    callbacks,
  };
}

/** Serializes the normalized value back into the `data-*-data` attribute shape. */
export function mediaDataAttribute(kind, normalized) {
  const fields = NESTED_FIELDS[kind];
  const out = { url: normalized.url };
  out[fields.name] = normalized.name;
  out[fields.size] = normalized.size;
  if (fields.mimeType) out[fields.mimeType] = normalized.mimeType;
  return out;
}

export function isMediaInsert(op) {
  const ins = op?.insert;
  if (!ins || typeof ins !== "object") return false;
  return MEDIA_BLOT_NAMES.some((n) =>
    Object.prototype.hasOwnProperty.call(ins, n),
  );
}

/**
 * Converts a Delta insert op into a clipboard item
 * `{ kind, url, name, size, mimeType? }`, or null when the op is not a media
 * insert or carries no URL.
 */
export function mediaItemFromInsert(op) {
  if (!isMediaInsert(op)) return null;
  const blotName = MEDIA_BLOT_NAMES.find((n) => op.insert[n] !== undefined);
  const kind = MEDIA_KIND_BY_BLOT[blotName];
  const raw = op.insert[blotName];
  if (!raw || typeof raw !== "object") return null;
  const n = normalizeMediaValue(kind, raw);
  if (typeof n.url !== "string" || n.url.length === 0) return null;
  const item = { kind, url: n.url, name: n.name, size: n.size };
  if (kind === "audio") item.mimeType = n.mimeType;
  return item;
}
