// W3-4 transformations applied when rows are added (DS-06). Stored per
// dataset as `dataset_config.schema.transforms: [{column, kind}]`; the
// Schema drawer previews them and the mock applies the same functions on
// every add path (IM1 file / dataset / production, paste, N2 add_row), so
// the UI and the server agree. Each transform is idempotent. Pure.

export const TRANSFORM_KINDS = Object.freeze([
  {
    kind: "remove_system_messages",
    label: "Remove system messages",
    short: "remove_system_messages",
  },
  {
    kind: "to_openai_messages",
    label: "Convert to OpenAI messages",
    short: "to_openai_messages",
  },
  {
    kind: "to_openai_tools",
    label: "Convert tools to OpenAI tools",
    short: "to_openai_tools",
  },
  {
    kind: "drop_unknown_fields",
    label: "Drop fields not in the schema",
    short: "drop_unknown_fields",
  },
  {
    kind: "trim_whitespace",
    label: "Trim whitespace",
    short: "trim_whitespace",
  },
]);
const KNOWN = new Set(TRANSFORM_KINDS.map((t) => t.kind));
export const transformLabel = (kind) =>
  TRANSFORM_KINDS.find((t) => t.kind === kind)?.label ?? kind;

const isObj = (v) => v != null && typeof v === "object" && !Array.isArray(v);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** A cell value as data (JSON text parsed), and how to give it back. */
function unwrap(value) {
  if (typeof value === "string") {
    const t = value.trim();
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        return { data: JSON.parse(t), back: (d) => JSON.stringify(d) };
      } catch {
        return { data: value, back: (d) => d };
      }
    }
  }
  return { data: value, back: (d) => d };
}

/** The message list of a value and a function that puts a new list back. */
function messagesOf(data) {
  if (Array.isArray(data)) return { list: data, put: (l) => l };
  if (isObj(data) && Array.isArray(data.turns))
    return { list: data.turns, put: (l) => ({ ...data, turns: l }) };
  if (isObj(data) && Array.isArray(data.messages))
    return { list: data.messages, put: (l) => ({ ...data, messages: l }) };
  return null;
}

const ROLE_MAP = {
  human: "user",
  user: "user",
  customer: "user",
  caller: "user",
  ai: "assistant",
  assistant: "assistant",
  agent: "assistant",
  bot: "assistant",
  gpt: "assistant",
  system: "system",
  tool: "tool",
  function: "tool",
};

/** `{speaker,text}`, `{from,value}`, LangChain `{type,content}` → OpenAI. */
function openAiMessage(m) {
  if (!isObj(m)) return m;
  const rawRole = m.role ?? m.type ?? m.from ?? m.speaker ?? m.author;
  const role = ROLE_MAP[String(rawRole ?? "").toLowerCase()] ?? rawRole;
  const content = m.content ?? m.text ?? m.value ?? m.message ?? "";
  const out = { role, content };
  if (m.tool_calls) out.tool_calls = m.tool_calls;
  if (m.name) out.name = m.name;
  if (m.tool_call_id) out.tool_call_id = m.tool_call_id;
  return out;
}

const roleOfMessage = (m) =>
  String(m?.role ?? m?.type ?? m?.from ?? m?.speaker ?? "").toLowerCase();

const TRANSFORMS = {
  remove_system_messages(data) {
    const msgs = messagesOf(data);
    if (!msgs) return { data, changed: 0 };
    const kept = msgs.list.filter((m) => roleOfMessage(m) !== "system");
    const removed = msgs.list.length - kept.length;
    return {
      data: removed ? msgs.put(kept) : data,
      changed: removed,
      note: removed ? `Removed ${plural(removed, "system message")}` : null,
    };
  },
  to_openai_messages(data) {
    const msgs = messagesOf(data);
    if (!msgs) return { data, changed: 0 };
    const next = msgs.list.map(openAiMessage);
    const changed = next.filter(
      (m, i) => JSON.stringify(m) !== JSON.stringify(msgs.list[i]),
    ).length;
    return {
      data: changed ? next : data,
      changed,
      note: changed
        ? `Converted ${plural(changed, "message")} to OpenAI messages`
        : null,
    };
  },
  to_openai_tools(data) {
    if (!Array.isArray(data)) return { data, changed: 0 };
    let changed = 0;
    const next = data.map((t) => {
      if (!isObj(t) || t.type === "function" || !t.name) return t;
      changed += 1;
      return {
        type: "function",
        function: {
          name: t.name,
          ...(t.description ? { description: t.description } : {}),
          parameters: t.parameters ??
            t.input_schema ??
            t.args ?? {
              type: "object",
              properties: {},
            },
        },
      };
    });
    return {
      data: changed ? next : data,
      changed,
      note: changed
        ? `Converted ${plural(changed, "tool")} to OpenAI tools`
        : null,
    };
  },
  drop_unknown_fields(data, { columnSchema } = {}) {
    const allowed = columnSchema?.properties
      ? new Set(Object.keys(columnSchema.properties))
      : null;
    if (!allowed || !isObj(data)) return { data, changed: 0 };
    const dropped = Object.keys(data).filter((k) => !allowed.has(k));
    if (!dropped.length) return { data, changed: 0 };
    return {
      data: Object.fromEntries(
        Object.entries(data).filter(([k]) => allowed.has(k)),
      ),
      changed: dropped.length,
      note: `Dropped ${plural(dropped.length, "field")} not in the schema (${dropped.join(", ")})`,
    };
  },
  trim_whitespace(data) {
    if (typeof data === "string") {
      const t = data.trim();
      return t === data
        ? { data, changed: 0 }
        : { data: t, changed: 1, note: "Trimmed whitespace" };
    }
    const msgs = messagesOf(data);
    if (!msgs) return { data, changed: 0 };
    let changed = 0;
    const next = msgs.list.map((m) => {
      if (!isObj(m) || typeof m.content !== "string") return m;
      const t = m.content.trim();
      if (t === m.content) return m;
      changed += 1;
      return { ...m, content: t };
    });
    return {
      data: changed ? msgs.put(next) : data,
      changed,
      note: changed ? "Trimmed whitespace" : null,
    };
  },
};

/** The dataset's transforms (`[{column, kind}]`, known kinds only). */
export const transformsOf = (schema) =>
  (schema?.transforms ?? []).filter((t) => t && t.column && KNOWN.has(t.kind));

/**
 * Apply the transforms to one row's cells (by column name).
 * @returns {{cells: object, notes: string[], applied: {column, kind}[]}}
 *   `notes` read `Removed 1 system message (conversation)`.
 */
export function applyTransforms(cellsByName, schema) {
  const list = transformsOf(schema);
  const cells = { ...(cellsByName ?? {}) };
  const notes = [];
  const applied = [];
  list.forEach(({ column, kind }) => {
    if (!(column in cells) || cells[column] == null || cells[column] === "")
      return;
    const { data, back } = unwrap(cells[column]);
    const res = TRANSFORMS[kind](data, {
      columnSchema: schema?.declared?.properties?.[column],
    });
    if (!res.changed) return;
    cells[column] = back(res.data);
    applied.push({ column, kind });
    if (res.note) notes.push(`${res.note} (${column})`);
  });
  return { cells, notes, applied };
}

/**
 * `Transforms: remove_system_messages on conversation (10 rows)` for the
 * version note, from the per-row `applied` lists.
 */
export function transformsNote(appliedPerRow = []) {
  const count = new Map();
  appliedPerRow.forEach((applied) =>
    [
      ...new Set((applied ?? []).map((a) => `${a.kind} on ${a.column}`)),
    ].forEach((k) => count.set(k, (count.get(k) ?? 0) + 1)),
  );
  if (!count.size) return null;
  return `Transforms: ${[...count.entries()]
    .map(([k, n]) => `${k} (${plural(n, "row")})`)
    .join(", ")}`;
}
