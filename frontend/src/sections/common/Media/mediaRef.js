// One media reference for every value (R4-SPEC D9, D10, D24; lane F; frozen
// API). It replaces extension-only detection and is used for inputs and
// outputs alike. Pure: no React, no DOM, no network.
//
//   mediaRefOf(value, {columnType}) → MediaRef | MediaRef[] | null
//   mediaLabel(ref) → `Image · screen-1.png` / `3 images` / `Call · 1 m 48 s`
//   conversationOf(value) → {turns, n, firstCustomer, failingTurn?} | null
// A conversation is not media: mediaRefOf stays null for it.
import { fmtMs } from "src/sections/common/format/fmt";

export const MEDIA_KINDS = Object.freeze([
  "image",
  "audio",
  "video",
  "document",
  "call",
  "file",
]);

const EXT = {
  image: ["png", "jpg", "jpeg", "gif", "webp", "svg", "avif", "bmp", "heic"],
  audio: ["wav", "mp3", "m4a", "ogg", "oga", "flac", "aac", "opus"],
  video: ["mp4", "mov", "webm", "mkv", "m4v", "avi"],
  document: ["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "odt", "rtf"],
};
const MIME_BY_EXT = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  avif: "image/avif",
  bmp: "image/bmp",
  heic: "image/heic",
  wav: "audio/wav",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  flac: "audio/flac",
  aac: "audio/aac",
  opus: "audio/opus",
  mp4: "video/mp4",
  mov: "video/quicktime",
  webm: "video/webm",
  mkv: "video/x-matroska",
  m4v: "video/mp4",
  avi: "video/x-msvideo",
  pdf: "application/pdf",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ppt: "application/vnd.ms-powerpoint",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  odt: "application/vnd.oasis.opendocument.text",
  rtf: "application/rtf",
};
const KIND_BY_EXT = Object.fromEntries(
  Object.entries(EXT).flatMap(([kind, exts]) => exts.map((e) => [e, kind])),
);
// Column types (existing datatypes) that hold media.
const COLUMN_KIND = {
  image: "image",
  images: "image",
  audio: "audio",
  video: "video",
  document: "document",
  file: "file",
};

const kindOfMime = (mime) => {
  const m = String(mime ?? "").toLowerCase();
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("audio/")) return "audio";
  if (m.startsWith("video/")) return "video";
  if (
    m === "application/pdf" ||
    m.includes("word") ||
    m.includes("presentation") ||
    m.includes("sheet")
  )
    return "document";
  return m ? "file" : null;
};

const nameOf = (url) => {
  if (!url || String(url).startsWith("data:")) return undefined;
  const clean = String(url).split(/[?#]/)[0];
  const last = clean.split("/").pop();
  try {
    return decodeURIComponent(last) || undefined;
  } catch {
    return last || undefined;
  }
};
const extOf = (url) => {
  const name = nameOf(url);
  const m = /\.([a-z0-9]{2,5})$/i.exec(name ?? "");
  return m ? m[1].toLowerCase() : null;
};
const looksLikeUrl = (s) =>
  /^(https?:\/\/|\/|\.\/|blob:|s3:\/\/|gs:\/\/)/i.test(s) && !/\s/.test(s);

const ref = (kind, url, mime, extra = {}) => {
  const out = { kind, mime: mime ?? "application/octet-stream", url };
  const name = extra.name ?? nameOf(url);
  if (name) out.name = name;
  Object.entries(extra).forEach(([k, v]) => {
    if (v != null && k !== "name") out[k] = v;
  });
  return out;
};

/** A `data:` URI → MediaRef. */
function fromDataUri(s) {
  const m = /^data:([^;,]+)[;,]/i.exec(s);
  if (!m) return null;
  const kind = kindOfMime(m[1]);
  return kind ? ref(kind, s, m[1].toLowerCase()) : null;
}

/** A URL string (by extension) → MediaRef, or null. */
function fromUrl(s, forcedKind) {
  if (s.startsWith("data:")) return fromDataUri(s);
  if (!looksLikeUrl(s)) return null;
  const ext = extOf(s);
  const kind = forcedKind ?? KIND_BY_EXT[ext];
  if (!kind) return null;
  return ref(kind, s, MIME_BY_EXT[ext]);
}

const tryJson = (s) => {
  const t = String(s).trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return undefined;
  try {
    return JSON.parse(t);
  } catch {
    return undefined;
  }
};

/** True for a CallValue (R4-SPEC D10). */
export const isCall = (value) =>
  Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      value.kind === "call" &&
      value.call_id,
  );

/** The CallValue in a value (object or JSON string), or null. */
export function callOf(value) {
  if (isCall(value)) return value;
  if (typeof value === "string") {
    const parsed = tryJson(value);
    return isCall(parsed) ? parsed : null;
  }
  return null;
}

/** One provider content part (OpenAI / Anthropic / fi.File) → MediaRef. */
function fromPart(part, forcedKind) {
  if (!part || typeof part !== "object") return null;
  // fi.File / {kind, url} dicts
  if (
    MEDIA_KINDS.includes(part.kind) &&
    typeof part.url === "string" &&
    part.kind !== "call"
  )
    return ref(part.kind, part.url, part.mime ?? MIME_BY_EXT[extOf(part.url)], {
      name: part.name,
      size_bytes: part.size_bytes,
      duration_ms: part.duration_ms,
      width: part.width,
      height: part.height,
      pages: part.pages,
      channels: part.channels,
      peaks: part.peaks,
      poster_url: part.poster_url,
      alt: part.alt,
    });
  // OpenAI image_url part
  if (part.type === "image_url") {
    const url =
      typeof part.image_url === "string" ? part.image_url : part.image_url?.url;
    if (!url) return null;
    return url.startsWith("data:")
      ? fromDataUri(url)
      : ref("image", url, MIME_BY_EXT[extOf(url)] ?? "image/*");
  }
  // OpenAI input_audio part
  if (part.type === "input_audio" && part.input_audio?.data) {
    const fmt = part.input_audio.format ?? "wav";
    return ref(
      "audio",
      `data:audio/${fmt};base64,${part.input_audio.data}`,
      `audio/${fmt}`,
    );
  }
  // OpenAI file part
  if (part.type === "file" && part.file) {
    const data = part.file.file_data;
    const r =
      typeof data === "string" && data.startsWith("data:")
        ? fromDataUri(data)
        : null;
    return r
      ? { ...r, ...(part.file.filename ? { name: part.file.filename } : {}) }
      : ref("document", data ?? "", "application/pdf", {
          name: part.file.filename,
        });
  }
  // Anthropic image / document blocks with a source
  if ((part.type === "image" || part.type === "document") && part.source) {
    const src = part.source;
    if (src.type === "base64" && src.data) {
      const mime =
        src.media_type ??
        (part.type === "image" ? "image/png" : "application/pdf");
      return ref(
        kindOfMime(mime) ?? part.type,
        `data:${mime};base64,${src.data}`,
        mime,
      );
    }
    if (src.type === "url" && src.url)
      return ref(
        part.type,
        src.url,
        src.media_type ?? MIME_BY_EXT[extOf(src.url)],
      );
  }
  void forcedKind;
  return null;
}

const single = (list) =>
  list.length === 0 ? null : list.length === 1 ? list[0] : list;

/**
 * The media in a value, or null for text / numbers / JSON / conversations.
 * Sniffs URLs by extension, `data:` URIs, OpenAI `image_url` / `input_audio`
 * / `file` parts, Anthropic `source` blocks, `{kind, url}` dicts, a
 * CallValue, arrays of any of these, and JSON strings holding them. A media
 * column type (`image images audio video document file`) forces the kind of
 * a bare URL.
 * @param {*} value
 * @param {{columnType?: string}} [opts]
 * @returns {object|object[]|null}  MediaRef, MediaRef[] (2+), or null
 */
export function mediaRefOf(value, { columnType } = {}) {
  const forced = COLUMN_KIND[columnType] ?? null;
  if (value == null || value === "") return null;
  const call = callOf(value);
  if (call)
    return ref(
      "call",
      call.recording?.stereo?.url ?? call.recording?.agent?.url ?? "",
      "application/vnd.futureagi.call+json",
      {
        name: call.call_id,
        duration_ms: call.duration_ms,
        channels: call.recording ? 2 : undefined,
      },
    );
  if (typeof value === "string") {
    const s = value.trim();
    const parsed = tryJson(s);
    if (parsed !== undefined) return mediaRefOf(parsed, { columnType });
    return fromUrl(s, forced);
  }
  if (Array.isArray(value)) {
    if (conversationOf(value)) {
      // Media inside message content parts still counts.
      const parts = value.flatMap((m) =>
        Array.isArray(m?.content) ? m.content : [],
      );
      return single(parts.map((p) => fromPart(p)).filter(Boolean));
    }
    const refs = value
      .map((v) =>
        typeof v === "string" ? fromUrl(v.trim(), forced) : fromPart(v, forced),
      )
      .filter(Boolean);
    return single(refs);
  }
  if (typeof value === "object") {
    const part = fromPart(value, forced);
    if (part) return part;
    if (typeof value.url === "string" && forced)
      return fromUrl(value.url, forced);
  }
  return null;
}

/** What kind of output a trial produced (R4 `trial.output_kind`, else sniffed). */
export function outputKindOf(output, { outputKind } = {}) {
  if (outputKind) return outputKind;
  if (output == null) return "text";
  if (callOf(output)) return "call";
  const media = mediaRefOf(output);
  if (media) return Array.isArray(media) ? media[0].kind : media.kind;
  if (conversationOf(output)) return "messages";
  if (typeof output === "object") return "json";
  if (typeof output === "string") {
    const parsed = tryJson(output);
    if (parsed && typeof parsed === "object") return "json";
  }
  return "text";
}

/**
 * A duration on media chips: under a minute the one formatter (`842 ms`,
 * `9.8 s`, `42 s`), else `1 m 48 s`.
 */
export function fmtDuration(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return fmtMs(null);
  if (n < 60000) return fmtMs(n);
  const total = Math.round(n / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m} m ${String(s).padStart(2, "0")} s`;
}

const KIND_WORD = {
  image: ["Image", "images"],
  audio: ["Audio", "audio clips"],
  video: ["Video", "videos"],
  document: ["Document", "documents"],
  call: ["Call", "calls"],
  file: ["File", "files"],
};

/**
 * A short label for a media value: `Image · screen-1.png`, `Audio · 42 s`,
 * `PDF · 3 pages`, `Call · 1 m 48 s`, `3 images`, `2 files` (mixed).
 * @param {object|object[]|null} r
 */
export function mediaLabel(r) {
  if (!r) return "";
  if (Array.isArray(r)) {
    const kinds = new Set(r.map((x) => x.kind));
    const kind = kinds.size === 1 ? r[0].kind : "file";
    return `${r.length} ${KIND_WORD[kind]?.[1] ?? "files"}`;
  }
  const word =
    r.kind === "document" && r.mime === "application/pdf"
      ? "PDF"
      : KIND_WORD[r.kind]?.[0] ?? "File";
  let detail = null;
  if (
    (r.kind === "audio" || r.kind === "video" || r.kind === "call") &&
    Number.isFinite(r.duration_ms)
  )
    detail = fmtDuration(r.duration_ms);
  else if (r.kind === "document" && Number.isFinite(r.pages))
    detail = `${r.pages} ${r.pages === 1 ? "page" : "pages"}`;
  else if (r.name) detail = r.name;
  return detail ? `${word} · ${detail}` : word;
}

// ------------------------------------------------------------ conversations

const CUSTOMER_ROLES = new Set(["user", "customer", "caller", "human"]);
const ROLE_OK = new Set([
  "system",
  "user",
  "assistant",
  "tool",
  "function",
  "customer",
  "agent",
  "caller",
  "human",
  "ai",
  "bot",
  "developer",
]);

const partText = (content) => {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((p) => {
        if (typeof p === "string") return p;
        if (p?.type === "text") return p.text ?? "";
        if (p?.type === "tool_use")
          return `${p.name}(${JSON.stringify(p.input ?? {})})`;
        if (p?.type === "tool_result") return partText(p.content);
        return "";
      })
      .filter(Boolean)
      .join("\n");
  return "";
};
const toolCallText = (calls) =>
  (calls ?? [])
    .map((c) => {
      const f = c.function ?? c;
      return `${f.name}(${typeof f.arguments === "string" ? f.arguments : JSON.stringify(f.arguments ?? {})})`;
    })
    .join("\n");

const messagesOf = (value) => {
  if (typeof value === "string") {
    const parsed = tryJson(value);
    return parsed === undefined ? null : messagesOf(parsed);
  }
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    if (Array.isArray(value.turns)) return value.turns;
    if (Array.isArray(value.messages)) return value.messages;
  }
  return null;
};

/**
 * A conversation in a value: `{turns: [{role, content|text}]}`,
 * `[{role, content}]`, OpenAI / Anthropic message arrays (string or part
 * content, tool calls), or a JSON string of any of these.
 * @returns {{turns: {role: string, text: string}[], n: number,
 *   firstCustomer: string|null, failingTurn?: number}|null}
 */
export function conversationOf(value) {
  const msgs = messagesOf(value);
  if (!Array.isArray(msgs) || !msgs.length) return null;
  const valid = msgs.every(
    (m) =>
      m &&
      typeof m === "object" &&
      typeof m.role === "string" &&
      ROLE_OK.has(m.role.toLowerCase()),
  );
  if (!valid) return null;
  const turns = msgs.map((m) => {
    const text =
      partText(m.content ?? m.text) || toolCallText(m.tool_calls) || "";
    return { role: m.role.toLowerCase(), text };
  });
  const first = turns.find((t) => CUSTOMER_ROLES.has(t.role) && t.text);
  const failIdx = msgs.findIndex((m) => m.failing || m.failed || m.is_failing);
  const failingTurn = Number.isInteger(value?.failing_turn)
    ? value.failing_turn
    : failIdx >= 0
      ? failIdx
      : undefined;
  return {
    turns,
    n: turns.length,
    firstCustomer: first?.text ?? null,
    ...(failingTurn !== undefined ? { failingTurn } : {}),
  };
}

/** The last customer turn of a conversation (D24 input mapping), or null. */
export function lastCustomerTurn(value) {
  const conv = conversationOf(value);
  if (!conv) return null;
  for (let i = conv.turns.length - 1; i >= 0; i -= 1)
    if (CUSTOMER_ROLES.has(conv.turns[i].role) && conv.turns[i].text)
      return conv.turns[i].text;
  return null;
}
