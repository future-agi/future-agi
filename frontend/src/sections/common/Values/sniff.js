// Typed value model (R2-SPEC P6 §3.1): one cell value → one of 18 kinds,
// sniffed per cell. The declared column type wins for editing and
// validation; the sniffed kind drives rendering. A value that does not fit
// its declared type is reported in `meta.mismatch` (the grid's amber
// corner), never thrown.
//
// Wire note: grid cells always arrive as text, so a declared type coerces
// first (`"0.83"` in a float column is a number) and only a value that does
// not coerce is a mismatch.
//
// Frozen (PRIMITIVES.md "Values"): sniffValue(value, {declaredType}) →
// {kind, sub, normalized, meta}. Pure; never throws.
import { parse as parseYaml } from "yaml";

export const KINDS = [
  "empty",
  "text",
  "markdown",
  "yaml_text",
  "number",
  "boolean",
  "enum",
  "datetime",
  "object",
  "array",
  "chat",
  "image",
  "images",
  "audio",
  "video",
  "document",
  "ref",
  "embedding",
];

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)(\?\S*)?$/i;
const AUDIO_EXT = /\.(mp3|wav|ogg|m4a|flac|aac|opus)(\?\S*)?$/i;
const VIDEO_EXT = /\.(mp4|webm|mov|m4v|mkv)(\?\S*)?$/i;
const DOC_EXT = /\.(pdf|docx?|pptx?|xlsx?|txt|csv)(\?\S*)?$/i;
const URL_LIKE = /^(https?:\/\/|\/)\S+$/i;
const NUMBER_TEXT = /^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;
const ISO_DATE =
  /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;
const EPOCH_MS = /^\d{12,13}$/;
const MD_SYNTAX =
  /(^|\n)(#{1,6} |[-*] |\d+\. |> |```)|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)/;
export const CHAT_ROLES = new Set([
  "system",
  "user",
  "assistant",
  "tool",
  "function",
  "developer",
]);
const LANGCHAIN_ROLE = {
  human: "user",
  ai: "assistant",
  system: "system",
  tool: "tool",
  function: "function",
};
const CHAT_FIELD = /conversation|messages|chat|history|transcript|turns/i;

const NUMBER_TYPES = new Set(["float", "integer", "number"]);
const JSON_TYPES = new Set([
  "json",
  "conversation",
  "checks",
  "trace",
  "array",
]);

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** `{ok, value}`: JSON text parsed (objects, arrays and string literals). */
export function tryJson(text) {
  if (typeof text !== "string") return { ok: false };
  const t = text.trim();
  if (!t) return { ok: false };
  const c = t[0];
  if (c !== "{" && c !== "[" && c !== '"') return { ok: false };
  try {
    return { ok: true, value: JSON.parse(t) };
  } catch {
    return { ok: false };
  }
}

/** Media kind of a URL / data URI, or null. */
export function mediaKindOfString(s) {
  if (typeof s !== "string") return null;
  const t = s.trim();
  if (t.startsWith("data:")) {
    const mime = t.slice(5, t.indexOf(";") > 0 ? t.indexOf(";") : 40);
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("audio/")) return "audio";
    if (mime.startsWith("video/")) return "video";
    if (mime === "application/pdf" || mime.includes("officedocument"))
      return "document";
    return null;
  }
  if (!URL_LIKE.test(t)) return null;
  if (IMAGE_EXT.test(t)) return "image";
  if (AUDIO_EXT.test(t)) return "audio";
  if (VIDEO_EXT.test(t)) return "video";
  if (DOC_EXT.test(t)) return "document";
  return null;
}

/** `contract.pdf` from a URL; `image.png` for data URIs. */
export function fileNameOf(url) {
  if (typeof url !== "string") return "";
  const t = url.trim();
  if (t.startsWith("data:")) {
    const mime = t.slice(5, t.indexOf(";") > 0 ? t.indexOf(";") : 40);
    const ext = mime.split("/")[1] ?? "bin";
    return `inline.${ext.replace("jpeg", "jpg")}`;
  }
  const clean = t.split(/[?#]/)[0];
  return decodeURIComponent(clean.split("/").pop() || clean);
}

/** Approximate size in bytes of a data URI payload (base64), else null. */
export function dataUriBytes(url) {
  if (typeof url !== "string" || !url.startsWith("data:")) return null;
  const at = url.indexOf("base64,");
  if (at < 0) return url.length;
  return Math.floor(((url.length - at - 7) * 3) / 4);
}

// ------------------------------------------------------------------ chat

const blockText = (b) =>
  typeof b === "string" ? b : b?.type === "text" ? b.text ?? "" : null;

/** Anthropic image `source` → data URI / URL. */
const anthropicImageUrl = (source) => {
  if (!source) return null;
  if (source.type === "base64")
    return `data:${source.media_type ?? "image/png"};base64,${source.data ?? ""}`;
  return source.url ?? null;
};

function isAnthropicBlocks(content) {
  return (
    Array.isArray(content) &&
    content.some(
      (b) =>
        isObj(b) &&
        ((b.type === "image" && isObj(b.source)) ||
          b.type === "tool_use" ||
          b.type === "tool_result"),
    )
  );
}

/**
 * Normalise a message list to OpenAI shape. Returns
 * `{messages, from: "OpenAI"|"Anthropic"|"LangChain"}` or null when the
 * value is not a message list.
 */
export function normalizeChat(value) {
  if (!Array.isArray(value) || !value.length) return null;
  if (!value.every(isObj)) return null;
  // LangChain `{type: "human"|"ai", content}`
  if (
    value.every((m) => typeof m.type === "string" && LANGCHAIN_ROLE[m.type]) &&
    value.every((m) => "content" in m)
  ) {
    return {
      from: "LangChain",
      messages: value.map((m) => ({
        role: LANGCHAIN_ROLE[m.type],
        content: m.content,
        ...(m.tool_calls ? { tool_calls: m.tool_calls } : {}),
      })),
    };
  }
  if (
    !value.every(
      (m) =>
        typeof m.role === "string" && ("content" in m || "tool_calls" in m),
    )
  )
    return null;
  const anthropic = value.some((m) => isAnthropicBlocks(m.content));
  if (!anthropic) return { from: "OpenAI", messages: value };
  const messages = [];
  value.forEach((m) => {
    if (!Array.isArray(m.content)) {
      messages.push(m);
      return;
    }
    const parts = [];
    const toolCalls = [];
    const toolResults = [];
    m.content.forEach((b) => {
      const text = blockText(b);
      if (text !== null) parts.push({ type: "text", text });
      else if (b?.type === "image")
        parts.push({
          type: "image_url",
          image_url: { url: anthropicImageUrl(b.source) },
        });
      else if (b?.type === "tool_use")
        toolCalls.push({
          id: b.id,
          type: "function",
          function: { name: b.name, arguments: JSON.stringify(b.input ?? {}) },
        });
      else if (b?.type === "tool_result")
        toolResults.push({
          role: "tool",
          tool_call_id: b.tool_use_id,
          content:
            typeof b.content === "string"
              ? b.content
              : JSON.stringify(b.content ?? null),
        });
    });
    if (parts.length || toolCalls.length)
      messages.push({
        role: m.role,
        content:
          parts.length === 1 && parts[0].type === "text"
            ? parts[0].text
            : parts.length
              ? parts
              : null,
        ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
      });
    messages.push(...toolResults);
  });
  return { from: "Anthropic", messages };
}

// integration (RV-02): speaker names sessions use, as chat roles.
const TURN_ROLE = {
  customer: "user",
  human: "user",
  agent: "assistant",
  bot: "assistant",
  ai: "assistant",
};

/** A session turn as an OpenAI message (roles and `{name, args}` calls). */
function turnMessage(t) {
  if (!isObj(t)) return t;
  const role = TURN_ROLE[t.role] ?? t.role;
  const calls = Array.isArray(t.tool_calls)
    ? t.tool_calls.map((c, i) =>
        isObj(c) && !c.function && typeof c.name === "string"
          ? {
              id: c.id ?? `call_${i + 1}`,
              type: "function",
              function: {
                name: c.name,
                arguments:
                  typeof c.args === "string"
                    ? c.args
                    : JSON.stringify(c.args ?? c.arguments ?? {}),
              },
            }
          : c,
      )
    : null;
  return { ...t, role, ...(calls ? { tool_calls: calls } : {}) };
}

/**
 * `{key, chat, extra}` when an object holds its conversation in `turns` or
 * `messages` (a list normalizeChat reads), else null.
 */
export function wrappedChatOf(v) {
  if (!isObj(v)) return null;
  const key = Array.isArray(v.turns)
    ? "turns"
    : Array.isArray(v.messages)
      ? "messages"
      : null;
  if (!key || !v[key].length) return null;
  const chat = normalizeChat(v[key].map(turnMessage));
  if (!chat) return null;
  const extra = Object.fromEntries(
    Object.entries(v).filter(([k]) => k !== key),
  );
  return { key, chat, extra };
}

/** Plain text of one message's content (images as `[image]`). */
export function messageText(m) {
  const c = m?.content;
  if (c == null) return "";
  if (typeof c === "string") return c;
  if (Array.isArray(c))
    return c
      .map((p) =>
        typeof p === "string"
          ? p
          : p?.type === "text"
            ? p.text
            : p?.type === "image_url" || p?.type === "image"
              ? "[image]"
              : p?.type === "input_audio"
                ? "[audio]"
                : "",
      )
      .filter(Boolean)
      .join(" ");
  return JSON.stringify(c);
}

/** Turns = messages other than `system`. */
export const chatTurns = (messages) =>
  (messages ?? []).filter((m) => m?.role !== "system").length;

// ------------------------------------------------------------------ yaml

function looksLikeYaml(text) {
  if (typeof text !== "string" || !text.includes("\n")) return false;
  const t = text.trim();
  if (!t || t.length > 20000) return false;
  if (t.startsWith("{") || t.startsWith("[")) return false;
  if (!/^[\w"'-][^\n]*:(\s|$)/m.test(t)) return false;
  // Structure beyond `Speaker: line` prose: a list item or an indented
  // continuation line (transcripts stay text).
  if (!/^\s*- \S/m.test(t) && !/^[ \t]+\S/m.test(t)) return false;
  try {
    const v = parseYaml(t);
    return v !== null && typeof v === "object";
  } catch {
    return false;
  }
}

// ----------------------------------------------------------------- sniff

const result = (kind, normalized, meta = {}, sub = null) => ({
  kind,
  sub,
  normalized,
  meta,
});

const quoteForTip = (s) => {
  const t = String(s);
  return JSON.stringify(t.length > 40 ? `${t.slice(0, 39)}…` : t);
};

const DECLARED_LABEL = {
  float: "number",
  integer: "number",
  number: "number",
  boolean: "boolean",
  datetime: "date",
  json: "JSON",
  conversation: "JSON",
  image: "image",
  images: "images",
  audio: "audio",
  video: "video",
  document: "document",
};

function sniffParsed(v, opts) {
  if (v === null || v === undefined) return result("empty", null);
  if (typeof v === "boolean") return result("boolean", v);
  if (typeof v === "number")
    return Number.isFinite(v) ? result("number", v) : result("text", String(v));
  if (typeof v === "string") return sniffString(v, opts);
  if (Array.isArray(v)) {
    if (!v.length)
      return CHAT_FIELD.test(opts.fieldName ?? "") ||
        opts.declaredType === "conversation"
        ? result("chat", [], { turns: 0, messages: 0, from: "OpenAI" })
        : result("array", v, { items: 0 });
    const chat = normalizeChat(v);
    if (chat)
      return result("chat", chat.messages, {
        turns: chatTurns(chat.messages),
        messages: chat.messages.length,
        from: chat.from,
        ...(chat.from !== "OpenAI" ? { normalizedFrom: chat.from } : {}),
      });
    if (v.length >= 64 && v.every((x) => typeof x === "number"))
      return result("embedding", v, { dims: v.length });
    if (
      v.every((x) => typeof x === "string" && mediaKindOfString(x) === "image")
    )
      return result("images", v, { count: v.length });
    return result("array", v, { items: v.length });
  }
  if (isObj(v)) {
    // OpenAI / Anthropic single media parts
    if (v.type === "image_url" && v.image_url?.url)
      return result("image", v.image_url.url, { part: "image_url" });
    if (v.type === "image" && isObj(v.source))
      return result("image", anthropicImageUrl(v.source), { part: "image" });
    if (v.type === "input_audio" && v.input_audio?.data)
      return result(
        "audio",
        `data:audio/${v.input_audio.format ?? "wav"};base64,${v.input_audio.data}`,
        { part: "input_audio" },
      );
    if (typeof v.trace_id === "string" && (v.spans != null || v.span_id))
      return result("ref", v, { ref: v.span_id ? "span" : "trace" });
    // integration (RV-02): a session object whose `turns` or `messages` list is a
    // conversation (`{turns: [{role, content}], resolution, …}`) reads as
    // chat; the other keys ride `meta.extra` (Raw shows the stored value).
    const wrapped = wrappedChatOf(v);
    if (wrapped)
      return result("chat", wrapped.chat.messages, {
        turns: chatTurns(wrapped.chat.messages),
        messages: wrapped.chat.messages.length,
        from: wrapped.chat.from,
        wrapped: wrapped.key,
        extra: wrapped.extra,
        ...(wrapped.chat.from !== "OpenAI"
          ? { normalizedFrom: wrapped.chat.from }
          : {}),
      });
    return result("object", v, { keys: Object.keys(v).length });
  }
  return result("text", String(v));
}

function sniffString(s, opts) {
  const t = s.trim();
  if (!t) return result("empty", null);
  const media = mediaKindOfString(t);
  if (media)
    return result(media, t, {
      fileName: fileNameOf(t),
      ...(t.startsWith("data:")
        ? { dataUri: true, bytes: dataUriBytes(t) }
        : {}),
    });
  const json = tryJson(t);
  if (json.ok && typeof json.value === "object" && json.value !== null)
    return { ...sniffParsed(json.value, opts), sub: "json_text" };
  if (opts.declaredType === "datetime" || ISO_DATE.test(t)) {
    const ms = EPOCH_MS.test(t) ? Number(t) : Date.parse(t);
    if (
      Number.isFinite(ms) &&
      (ISO_DATE.test(t) ||
        (EPOCH_MS.test(t) && opts.declaredType === "datetime"))
    )
      return result("datetime", new Date(ms).toISOString(), { ms });
  }
  if (t.length >= 2 && looksLikeYaml(s)) return result("yaml_text", s);
  if (MD_SYNTAX.test(s)) return result("markdown", s, { length: s.length });
  return result("text", s, { length: s.length });
}

/**
 * Sniff one value.
 * @param {*} value  as stored (text on the wire, or typed)
 * @param {{declaredType?: string, fieldName?: string, choices?: string[]}} [opts]
 * @returns {{kind: string, sub: string|null, normalized: *, meta: object}}
 *   `meta.mismatch` is the tooltip text when the value does not fit the
 *   declared type (`Text in a number column: "0.8"`).
 */
export function sniffValue(value, opts = {}) {
  const declaredType = opts.declaredType ?? null;
  if (
    value === null ||
    value === undefined ||
    (typeof value === "string" && !value.trim())
  )
    return result("empty", null);

  // Declared-type coercion (grid text → typed value).
  if (typeof value === "string" && declaredType) {
    const t = value.trim();
    if (NUMBER_TYPES.has(declaredType)) {
      if (NUMBER_TEXT.test(t)) return result("number", Number(t));
      const json = tryJson(t);
      const shown = json.ok && typeof json.value === "string" ? json.value : t;
      const r = json.ok
        ? sniffParsed(json.value, opts)
        : sniffString(value, opts);
      return {
        ...r,
        kind: r.kind === "number" ? "text" : r.kind,
        meta: {
          ...r.meta,
          mismatch: `Text in a number column: ${quoteForTip(shown)}`,
        },
      };
    }
    if (declaredType === "boolean") {
      const l = t.toLowerCase();
      if (["true", "false", "yes", "no", "passed", "failed"].includes(l))
        return result("boolean", ["true", "yes", "passed"].includes(l));
      return {
        ...sniffString(value, opts),
        meta: { mismatch: `Text in a boolean column: ${quoteForTip(t)}` },
      };
    }
    if (declaredType === "enum") {
      const choices = opts.choices;
      return result(
        "enum",
        t,
        choices?.length && !choices.includes(t)
          ? {
              mismatch: `${quoteForTip(t)} is not one of ${choices.join(", ")}`,
            }
          : {},
        t,
      );
    }
    if (declaredType === "images") {
      const json = tryJson(t);
      if (json.ok && Array.isArray(json.value))
        return result(
          "images",
          json.value.filter((x) => typeof x === "string"),
          { count: json.value.length },
        );
    }
    if (JSON_TYPES.has(declaredType)) {
      const json = tryJson(t);
      if (!json.ok)
        return {
          ...sniffString(value, opts),
          meta: {
            mismatch: `Not valid JSON in a JSON column`,
            invalidJson: true,
          },
        };
      const r = sniffParsed(json.value, opts);
      return { ...r, sub: "json_text" };
    }
    if (["image", "audio", "video", "document"].includes(declaredType)) {
      const media = mediaKindOfString(t);
      const r = sniffString(value, opts);
      if (!media && URL_LIKE.test(t))
        return result(declaredType, t, { fileName: fileNameOf(t) });
      if (media && media !== declaredType)
        return {
          ...r,
          meta: {
            ...r.meta,
            mismatch: `A ${media} in ${declaredType === "image" ? "an" : "a"} ${DECLARED_LABEL[declaredType]} column`,
          },
        };
      return r;
    }
  }
  return sniffParsed(value, { ...opts, declaredType });
}

/** Short label for a kind (`Number`, `Chat`, `YAML text`). */
export const KIND_LABEL = {
  empty: "Empty",
  text: "Text",
  markdown: "Markdown",
  yaml_text: "YAML text",
  number: "Number",
  boolean: "Boolean",
  enum: "Enum",
  datetime: "Date",
  object: "Object",
  array: "List",
  chat: "Chat",
  image: "Image",
  images: "Images",
  audio: "Audio",
  video: "Video",
  document: "Document",
  ref: "Trace",
  embedding: "Embedding",
  string: "String",
  null: "Null",
};

/** JSON kind of a parsed value: string · number · boolean · null · object · array. */
export function jsonKind(v) {
  if (v === null || v === undefined) return "null";
  if (Array.isArray(v)) return "array";
  if (typeof v === "object") return "object";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  return "string";
}
