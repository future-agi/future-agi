// The one file writer of every export screen (W3-6 B1): dataset Download…,
// results Download…, and any later export (selection, SDK parity). Pure:
// rows in, file text or bytes out; nothing here reads the page or the API.
//
//   toCsv(rows, columns, {contextLine})   RFC 4180 (CRLF, quotes doubled)
//   toJson({meta, rows})                  one JSON document
//   toJsonl(rows)                         one JSON object per line
//   toParquet(rows, columns)              Parquet 1.0: one row group,
//                                         uncompressed, PLAIN values,
//                                         RLE definition levels
//   toOpenAiFineTune(rows, roles)         {"messages":[…]} per line
//   toOpenAiEvals(rows, roles)            {"input":[…],"ideal":…} per line
//
// `columns` = `[{key, label?, type?}]` (or plain key strings): `key` reads
// the row object, `label` names the column in the file (defaults to the
// key), `type` (Parquet only: "BOOLEAN" | "INT64" | "DOUBLE") is the
// column's declared type, used when every value fits it.

/** @typedef {{key: string, label?: string, type?: string}} ExportColumn */

const asColumns = (columns) =>
  (columns ?? []).map((c) =>
    typeof c === "string"
      ? { key: c, label: c }
      : { key: c.key, label: c.label ?? c.key, type: c.type },
  );

const isBlank = (v) => v === null || v === undefined;

/** A cell as text: objects as JSON, null as empty. */
export function cellText(value) {
  if (isBlank(value)) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const csvField = (value) => {
  const s = cellText(value);
  return /[",\r\n]/.test(s) || /^\s|\s$/.test(s)
    ? `"${s.replace(/"/g, '""')}"`
    : s;
};

/**
 * RFC 4180 CSV: a header line, then one line per row, CRLF line breaks, a
 * field quoted when it holds a comma, a quote, a line break or edge spaces.
 * `contextLine` (off by default) adds one `# …` line before the header.
 * @param {object[]} rows
 * @param {(ExportColumn|string)[]} columns
 * @param {{contextLine?: string}} [opts]
 */
export function toCsv(rows, columns, { contextLine } = {}) {
  const cols = asColumns(columns);
  const lines = [];
  if (contextLine)
    lines.push(`# ${String(contextLine).replace(/[\r\n]+/g, " ")}`);
  lines.push(cols.map((c) => csvField(c.label)).join(","));
  (rows ?? []).forEach((row) =>
    lines.push(cols.map((c) => csvField(row?.[c.key])).join(",")),
  );
  return `${lines.join("\r\n")}\r\n`;
}

/** One JSON document `{meta, rows}` (2-space indent, trailing newline). */
export function toJson({ meta = {}, rows = [] } = {}) {
  return `${JSON.stringify({ meta, rows }, null, 2)}\n`;
}

/** One JSON object per line (`\n`), a trailing newline after the last. */
export function toJsonl(rows) {
  const list = rows ?? [];
  return list.length
    ? `${list.map((r) => JSON.stringify(r ?? null)).join("\n")}\n`
    : "";
}

// ------------------------------------------------------------- messages

const MESSAGE_ROLES = new Set(["system", "user", "assistant", "tool"]);

const parseMaybeJson = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!t || !"[{".includes(t[0])) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

/** Message content: text stays text; anything else becomes its JSON. */
const contentOf = (value) => cellText(parseMaybeJson(value));

/**
 * A conversation value (`[{role, content}]`, or `{messages: […]}`) as
 * OpenAI chat messages; anything else is not a conversation (null).
 */
export function conversationTurns(value) {
  const v = parseMaybeJson(value);
  const list = Array.isArray(v)
    ? v
    : Array.isArray(v?.messages)
      ? v.messages
      : null;
  if (!list) return null;
  const turns = [];
  for (const m of list) {
    if (!m || typeof m !== "object") return null;
    const role = String(m.role ?? "").toLowerCase();
    if (!MESSAGE_ROLES.has(role)) return null;
    turns.push({ role, content: contentOf(m.content ?? m.text ?? "") });
  }
  return turns;
}

/**
 * Expected tool calls (`[{name, args|arguments}]`) as OpenAI `tool_calls`
 * (`arguments` is a JSON string); null when the value is not that shape.
 */
export function openAiToolCalls(value) {
  const v = parseMaybeJson(value);
  if (!Array.isArray(v) || !v.length) return null;
  if (
    !v.every(
      (c) =>
        c &&
        typeof c === "object" &&
        typeof (c.name ?? c.function?.name) === "string",
    )
  )
    return null;
  return v.map((c, i) => {
    const name = c.name ?? c.function.name;
    const args = c.args ?? c.arguments ?? c.function?.arguments ?? {};
    return {
      id: `call_${i + 1}`,
      type: "function",
      function: {
        name,
        arguments: typeof args === "string" ? args : JSON.stringify(args),
      },
    };
  });
}

/**
 * The prompt messages of one row: optional system text, the prior turns
 * of the `turns` column, then the user message.
 * @param {object} row
 * @param {{system?: string, user: string, turns?: string}} roles
 */
function promptMessages(row, roles) {
  const out = [];
  if (roles.system) out.push({ role: "system", content: String(roles.system) });
  if (roles.turns) out.push(...(conversationTurns(row?.[roles.turns]) ?? []));
  const user = row?.[roles.user];
  const asTurns = conversationTurns(user);
  if (asTurns) out.push(...asTurns);
  else out.push({ role: "user", content: contentOf(user) });
  return out;
}

/**
 * Whether a row can be a training / eval example: it has an expected reply
 * (or, for fine-tuning, expected tool calls) and a user message.
 * @param {object} row
 * @param {{user: string, assistant: string, toolCalls?: string}} roles
 */
export function hasExpected(row, roles) {
  const reply = contentOf(row?.[roles.assistant]);
  const calls = roles.toolCalls
    ? openAiToolCalls(row?.[roles.toolCalls])
    : null;
  const user = row?.[roles.user];
  return Boolean(reply.trim() || calls) && !isBlank(user) && user !== "";
}

/**
 * OpenAI fine-tuning JSONL (chat format), one line per row that has an
 * expected value (rows without one are left out; `hasExpected` counts them):
 * `{"messages":[{"role":"system",…}?, …turns, {"role":"user",…},
 * {"role":"assistant","content":<expected>, "tool_calls"?:[…]}]}`.
 * @param {object[]} rows
 * @param {{system?: string, user: string, assistant: string, turns?: string,
 *   toolCalls?: string}} roles  row keys (system is literal text)
 */
export function toOpenAiFineTune(rows, roles) {
  return toJsonl(
    (rows ?? [])
      .filter((row) => hasExpected(row, roles))
      .map((row) => {
        const assistant = {
          role: "assistant",
          content: contentOf(row?.[roles.assistant]),
        };
        const calls = roles.toolCalls
          ? openAiToolCalls(row?.[roles.toolCalls])
          : null;
        if (calls) assistant.tool_calls = calls;
        return { messages: [...promptMessages(row, roles), assistant] };
      }),
  );
}

/**
 * OpenAI Evals JSONL, one line per row with an expected reply:
 * `{"input":[…messages], "ideal": <expected>}`.
 * @param {object[]} rows
 * @param {{system?: string, user: string, assistant: string, turns?: string}} roles
 */
export function toOpenAiEvals(rows, roles) {
  const evalRoles = { ...roles, toolCalls: undefined };
  return toJsonl(
    (rows ?? [])
      .filter((row) => hasExpected(row, evalRoles))
      .map((row) => ({
        input: promptMessages(row, roles),
        ideal: contentOf(row?.[roles.assistant]),
      })),
  );
}

// -------------------------------------------------------------- parquet
// Format: github.com/apache/parquet-format (parquet.thrift). Metadata is
// Thrift compact protocol; every column is OPTIONAL (max definition level
// 1, no repetition), so a page is `def levels (RLE, 4-byte length prefix)
// + PLAIN values of the non-null cells`.

const PQ_TYPE = { BOOLEAN: 0, INT64: 2, DOUBLE: 5, BYTE_ARRAY: 6 };
const ENC = { PLAIN: 0, RLE: 3 };
const UTF8_CONVERTED = 0;
const OPTIONAL = 1;
const MAX_SAFE = Number.MAX_SAFE_INTEGER;

const FITS = {
  BOOLEAN: (v) => typeof v === "boolean",
  INT64: (v) =>
    typeof v === "number" && Number.isInteger(v) && Math.abs(v) <= MAX_SAFE,
  DOUBLE: (v) => typeof v === "number" && Number.isFinite(v),
};

/**
 * The Parquet physical type of a column: its declared `type` when every
 * value fits it (a float column of whole numbers stays DOUBLE), else from
 * its values.
 */
export function parquetTypeOf(values, declared) {
  const present = values.filter((v) => !isBlank(v));
  if (FITS[declared] && present.every(FITS[declared])) return declared;
  if (!present.length) return "BYTE_ARRAY";
  if (present.every((v) => typeof v === "boolean")) return "BOOLEAN";
  if (present.every((v) => typeof v === "number" && Number.isFinite(v))) {
    return present.every((v) => Number.isInteger(v) && Math.abs(v) <= MAX_SAFE)
      ? "INT64"
      : "DOUBLE";
  }
  return "BYTE_ARRAY";
}

class Bytes {
  constructor() {
    this.parts = [];
    this.length = 0;
  }

  push(arr) {
    const u = arr instanceof Uint8Array ? arr : Uint8Array.from(arr);
    this.parts.push(u);
    this.length += u.length;
    return this;
  }

  u32(n) {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setUint32(0, n, true);
    return this.push(b);
  }

  concat() {
    const out = new Uint8Array(this.length);
    let at = 0;
    this.parts.forEach((p) => {
      out.set(p, at);
      at += p.length;
    });
    return out;
  }
}

const utf8 = (s) => new TextEncoder().encode(String(s));

/** Unsigned LEB128 of a non-negative safe integer. */
function varint(n) {
  const out = [];
  let v = n;
  while (v >= 0x80) {
    out.push(v % 0x80 | 0x80);
    v = Math.floor(v / 0x80);
  }
  out.push(v);
  return out;
}
const zigzag = (n) => (n >= 0 ? n * 2 : -n * 2 - 1);

// Thrift compact protocol writer (only what parquet.thrift needs here).
const CT = { I32: 5, I64: 6, BINARY: 8, LIST: 9, STRUCT: 12 };

class Thrift {
  constructor() {
    this.out = [];
    this.last = [0];
  }

  field(id, type) {
    const delta = id - this.last[this.last.length - 1];
    if (delta > 0 && delta <= 15) this.out.push((delta << 4) | type);
    else this.out.push(type, ...varint(zigzag(id)));
    this.last[this.last.length - 1] = id;
  }

  i32(id, v) {
    this.field(id, CT.I32);
    this.out.push(...varint(zigzag(v)));
  }

  i64(id, v) {
    this.field(id, CT.I64);
    this.out.push(...varint(zigzag(v)));
  }

  string(id, s) {
    this.field(id, CT.BINARY);
    const b = utf8(s);
    this.out.push(...varint(b.length), ...b);
  }

  begin(id) {
    this.field(id, CT.STRUCT);
    this.last.push(0);
  }

  end() {
    this.out.push(0);
    this.last.pop();
  }

  listHeader(id, elemType, size) {
    this.field(id, CT.LIST);
    if (size < 15) this.out.push((size << 4) | elemType);
    else this.out.push(0xf0 | elemType, ...varint(size));
  }

  listI32(id, values) {
    this.listHeader(id, CT.I32, values.length);
    values.forEach((v) => this.out.push(...varint(zigzag(v))));
  }

  listString(id, values) {
    this.listHeader(id, CT.BINARY, values.length);
    values.forEach((s) => {
      const b = utf8(s);
      this.out.push(...varint(b.length), ...b);
    });
  }

  /** A list of structs; `write(t, item)` writes one struct's fields. */
  listStruct(id, items, write) {
    this.listHeader(id, CT.STRUCT, items.length);
    items.forEach((item) => {
      this.last.push(0);
      write(this, item);
      this.out.push(0);
      this.last.pop();
    });
  }

  bytes() {
    return Uint8Array.from(this.out);
  }
}

/** RLE / bit-packed hybrid runs of 0/1 definition levels (bit width 1). */
function definitionLevels(defs) {
  const out = [];
  let i = 0;
  while (i < defs.length) {
    let j = i;
    while (j < defs.length && defs[j] === defs[i]) j += 1;
    out.push(...varint((j - i) * 2), defs[i]);
    i = j;
  }
  return out;
}

function plainValues(type, values) {
  const b = new Bytes();
  if (type === "BOOLEAN") {
    const packed = new Uint8Array(Math.ceil(values.length / 8));
    values.forEach((v, i) => {
      if (v) packed[i >> 3] |= 1 << (i & 7);
    });
    return b.push(packed).concat();
  }
  if (type === "INT64" || type === "DOUBLE") {
    const buf = new Uint8Array(values.length * 8);
    const dv = new DataView(buf.buffer);
    values.forEach((v, i) => {
      if (type === "INT64") dv.setBigInt64(i * 8, BigInt(v), true);
      else dv.setFloat64(i * 8, v, true);
    });
    return b.push(buf).concat();
  }
  values.forEach((v) => {
    const s = utf8(cellText(v));
    b.u32(s.length).push(s);
  });
  return b.concat();
}

/**
 * Parquet file bytes: one row group, one uncompressed PLAIN data page per
 * column. A column's declared `type` wins when every value fits it;
 * otherwise booleans → BOOLEAN, whole numbers → INT64, other numbers →
 * DOUBLE, everything else → UTF8 BYTE_ARRAY (objects as their JSON).
 * @param {object[]} rows
 * @param {(ExportColumn|string)[]} columns
 * @returns {Uint8Array}
 */
export function toParquet(rows, columns) {
  const cols = asColumns(columns);
  const list = rows ?? [];
  const file = new Bytes().push(utf8("PAR1"));
  const chunks = cols.map((c) => {
    const raw = list.map((r) => r?.[c.key]);
    const type = parquetTypeOf(raw, c.type);
    const defs = raw.map((v) => (isBlank(v) ? 0 : 1));
    const present = raw.filter((v) => !isBlank(v));
    const levels = definitionLevels(defs);
    const data = new Bytes()
      .u32(levels.length)
      .push(levels)
      .push(plainValues(type, present))
      .concat();
    const header = new Thrift();
    header.i32(1, 0); // DATA_PAGE
    header.i32(2, data.length);
    header.i32(3, data.length);
    header.begin(5);
    header.i32(1, list.length);
    header.i32(2, ENC.PLAIN);
    header.i32(3, ENC.RLE);
    header.i32(4, ENC.RLE);
    header.end();
    header.out.push(0);
    const head = header.bytes();
    const offset = file.length;
    file.push(head).push(data);
    return {
      column: c,
      type,
      offset,
      size: head.length + data.length,
      values: list.length,
    };
  });

  const meta = new Thrift();
  meta.i32(1, 1);
  meta.listStruct(
    2,
    [{ root: true }, ...chunks.map((k) => ({ chunk: k }))],
    (t, el) => {
      if (el.root) {
        t.string(4, "schema");
        t.i32(5, chunks.length);
        return;
      }
      const { chunk } = el;
      t.i32(1, PQ_TYPE[chunk.type]);
      t.i32(3, OPTIONAL);
      t.string(4, chunk.column.label);
      if (chunk.type === "BYTE_ARRAY") {
        t.i32(6, UTF8_CONVERTED);
        t.begin(10); // LogicalType
        t.begin(1); // STRING
        t.end();
        t.end();
      }
    },
  );
  meta.i64(3, list.length);
  meta.listStruct(4, [chunks], (t, group) => {
    t.listStruct(1, group, (tc, chunk) => {
      tc.i64(2, chunk.offset);
      tc.begin(3);
      tc.i32(1, PQ_TYPE[chunk.type]);
      tc.listI32(2, [ENC.PLAIN, ENC.RLE]);
      tc.listString(3, [chunk.column.label]);
      tc.i32(4, 0); // UNCOMPRESSED
      tc.i64(5, chunk.values);
      tc.i64(6, chunk.size);
      tc.i64(7, chunk.size);
      tc.i64(9, chunk.offset);
      tc.end();
    });
    t.i64(
      2,
      group.reduce((n, k) => n + k.size, 0),
    );
    t.i64(3, list.length);
  });
  meta.string(6, "Future AGI dataset export");
  meta.out.push(0);
  const footer = meta.bytes();
  file.push(footer).u32(footer.length).push(utf8("PAR1"));
  return file.concat();
}

// ---------------------------------------------------------- file naming

export const EXPORT_FORMATS = {
  csv: { label: "CSV", ext: "csv", mime: "text/csv" },
  json: { label: "JSON", ext: "json", mime: "application/json" },
  jsonl: { label: "JSONL", ext: "jsonl", mime: "application/x-ndjson" },
  parquet: {
    label: "Parquet",
    ext: "parquet",
    mime: "application/vnd.apache.parquet",
  },
  openai_ft: {
    label: "OpenAI fine-tune JSONL",
    ext: "jsonl",
    mime: "application/x-ndjson",
  },
  openai_evals: {
    label: "OpenAI Evals JSONL",
    ext: "jsonl",
    mime: "application/x-ndjson",
  },
};

/** `support-agent-golden-set-v3-smoke.jsonl` from its parts. */
export function exportFileName(parts, format) {
  const slug = (parts ?? [])
    .filter((p) => p !== null && p !== undefined && p !== "")
    .map((p) =>
      String(p)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, ""),
    )
    .filter(Boolean)
    .join("-")
    .slice(0, 120);
  const ext = EXPORT_FORMATS[format]?.ext ?? "txt";
  return `${slug || "export"}${format === "openai_ft" ? "-finetune" : format === "openai_evals" ? "-evals" : ""}.${ext}`;
}

/** `248 rows · about 1.2 MB` size text. */
export function sizeText(bytes) {
  const b = Number(bytes) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round((b / 1024) * 10) / 10} KB`;
  return `${Math.round((b / (1024 * 1024)) * 10) / 10} MB`;
}

/** Byte length of a writer's output (text as UTF-8, bytes as is). */
export function byteLength(out) {
  if (out instanceof Uint8Array) return out.length;
  return utf8(out ?? "").length;
}
