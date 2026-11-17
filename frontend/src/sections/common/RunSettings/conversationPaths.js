// W6-2 / W6-9 (A4, `data:replay:failing-turn`, `review:rows-send`): paths
// on a conversation cell. A conversation object (`{turns: [...]}` or
// `{messages: [...]}`, the conversation data type; read with the shared
// reader `Media/mediaRef.conversationOf`) has no elements of its own, so a
// path reads its turns:
//   [-1]          the last customer turn (what "last customer turn" maps)
//   [-2]          the customer turn before it, and so on
//   [-1:agent]    the last agent turn (also `:assistant`)
//   [-1:any]      the last turn of any role
//   [0], [1] ...  the turns in order, 0-based like every FA Path (faPath.js)
//   [*]           every turn, one per line
// then `.content` / `.text` (the turn's text, the default), `.role`, or any
// other key of the raw turn. A bare message array is not a conversation
// object: FA Paths index it as a list (`[-1]` its last element), so this
// reader leaves it to faPath.js. Pure; the Data step preview, Review's "Each
// row sends" line and the run engine (sources/conversation.js) all read a
// mapped value through `conversationPathValue`, so they agree.
import { conversationOf } from "src/sections/common/Media/mediaRef";
import { fromLegacyMapping } from "src/sections/common/Values/faPath";

const CUSTOMER = new Set(["user", "customer", "caller", "human"]);
const AGENT = new Set(["assistant", "agent", "ai", "bot"]);
const PATH_RE =
  /^\$?\[\s*(\*|-?\d+)\s*(?::\s*(agent|assistant|customer|user|any)\s*)?\]((?:\.[A-Za-z_][\w]*)*)$/;

const tryJson = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};

/** The raw turns of a conversation object, or null (arrays included). */
function rawTurnsOf(value) {
  const v = tryJson(value);
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  if (Array.isArray(v.turns)) return v.turns;
  if (Array.isArray(v.messages)) return v.messages;
  return null;
}

/** `{index, role, field}` of a conversation path, or null. */
export function parseConversationPath(path) {
  const m = PATH_RE.exec(String(path ?? "").trim());
  if (!m) return null;
  const fields = (m[3] ?? "").split(".").filter(Boolean);
  if (fields.length > 1) return null;
  return {
    index: m[1] === "*" ? "*" : Number(m[1]),
    role: m[2] ?? null,
    field: fields[0] ?? null,
  };
}

const roleFits = (role, want) => {
  if (want === "any") return true;
  if (want === "agent" || want === "assistant") return AGENT.has(role);
  return CUSTOMER.has(role);
};

/**
 * The text a path reads on a conversation cell: `{found, text}`, or null
 * when the value is not a conversation or the path is not a turn path (the
 * caller's own path reader decides then).
 */
export function conversationPathValue(value, path) {
  const p = parseConversationPath(path);
  if (!p) return null;
  const raw = rawTurnsOf(value);
  if (!raw) return null;
  const conv = conversationOf(tryJson(value));
  if (!conv) return null;
  const turns = conv.turns.map((t, i) => ({ ...t, raw: raw[i] ?? {} }));
  const read = (t) => {
    if (!t) return "";
    if (!p.field || p.field === "content" || p.field === "text") return t.text;
    if (p.field === "role") return t.role;
    const v = t.raw?.[p.field];
    return v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
  };
  if (p.index === "*") {
    const all = turns.filter((t) => !p.role || roleFits(t.role, p.role));
    const text = all.map(read).filter(Boolean).join("\n");
    return { found: Boolean(text), text };
  }
  if (p.index >= 0 && !p.role) {
    const t = turns[p.index];
    return { found: Boolean(t), text: read(t) };
  }
  const pool = turns.filter((t) => roleFits(t.role, p.role ?? "customer"));
  const k = p.index < 0 ? pool.length + p.index : p.index;
  const t = pool[k];
  return { found: Boolean(t), text: read(t) };
}

/**
 * A mapping value (`<column_id>::$[-1].content`) read on a cell's value:
 * `{found, text}` or null when it is not a turn path on a conversation.
 */
export function mappedConversationValue(mapping, cellValue) {
  if (typeof mapping !== "string") return null;
  const { path } = fromLegacyMapping(mapping);
  if (!path) return null;
  return conversationPathValue(cellValue, path);
}
