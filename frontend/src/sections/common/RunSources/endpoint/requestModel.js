// The one request an HTTP endpoint run sends (W6 B1). Pure, no clock, no
// randomness: `buildRequest` turns the card's endpoint and one row into
// `{method, url, headers, body}`. The browser Test (testEndpoint.js), Future
// AGI's endpoint test (MOCK handlers/r4-sources.js) and every try of the run
// (MOCK fixtures/world/sources/endpoint.js) call it, so Test on a row sends
// byte for byte what the run's try 1 sends for that row.
//
// - `{{name}}` resolves through the Data step's mapping first, then by
//   column name, then `row_id`; a name nothing supplies stays as written
//   and is listed in `missing`.
// - Inside a JSON string literal a value is JSON-escaped; outside one it is
//   written as a JSON value. In the URL it is URL-encoded.
// - Headers keep secrets as references (`secret_ref`): the request never
//   holds a secret's value. `headerLines` shows them as
//   `•••• (from secret NAME)` (Future AGI sends them) or as not sent (a
//   browser cannot read workspace secrets).
// - `traceparent` is the W3C header of the try's trace (version 00, sampled).
import {
  fromLegacyMapping,
  resolvePath,
} from "src/sections/common/Values/faPath";

const VAR_RE = /\{\{\s*([^{}]+?)\s*\}\}/g;
export const SECRET_MASK = "••••";

/** A cell's plain value (`{cell_value}` / `{cellValue}` unwrapped). */
export const cellOf = (v) =>
  v && typeof v === "object" && ("cell_value" in v || "cellValue" in v)
    ? v.cell_value ?? v.cellValue
    : v;

const textOf = (v) => {
  if (v == null) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
};

/** Values keyed by column id from an experiment sample row (FE rows). */
export function valuesById(row, columns) {
  if (!row) return {};
  const out = {};
  (columns ?? []).forEach((c) => {
    const v = cellOf(row[c.id] ?? row.cells?.[c.id] ?? row.values?.[c.id]);
    if (v !== undefined) out[c.id] = v;
  });
  return out;
}

/**
 * The value `{{name}}` takes on a row, or `undefined` when nothing supplies
 * it. A mapped or named column with no cell resolves to "" (the run's rule).
 */
export function variableValue(name, { mapping, values, columns, rowId } = {}) {
  const m = mapping?.[name];
  if (m && typeof m === "object") {
    if (m.kind === "empty") return "";
    if (m.kind === "constant") return String(m.value ?? "");
  }
  const vals = values ?? {};
  if (typeof m === "string" && m) {
    const { colId, path } = fromLegacyMapping(m);
    const raw = cellOf(vals[colId]);
    if (!path) return raw ?? "";
    const got = resolvePath(raw, path);
    return got.found ? got.value ?? "" : "";
  }
  const col = (columns ?? []).find((c) => c.name === name);
  if (col) return cellOf(vals[col.id]) ?? "";
  if (name === "row_id" && rowId != null) return String(rowId);
  return undefined;
}

const escapeJson = (s) => JSON.stringify(String(s)).slice(1, -1);
const asJsonValue = (v) =>
  typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v ?? null);

/** True when a body template is JSON (it starts with `{` or `[`). */
export const isJsonTemplate = (t) => /^\s*[{[]/.test(String(t ?? ""));

/**
 * Fill a JSON body template. A placeholder inside a string literal gets the
 * escaped text; one outside a string gets a JSON value.
 */
function fillJson(template, resolve) {
  const src = String(template ?? "");
  let out = "";
  let inStr = false;
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === "{" && src[i + 1] === "{") {
      const end = src.indexOf("}}", i + 2);
      if (end > 0) {
        const name = src.slice(i + 2, end).trim();
        const v = resolve(name);
        if (v === undefined) out += src.slice(i, end + 2);
        else out += inStr ? escapeJson(textOf(v)) : asJsonValue(v);
        i = end + 1;
        continue;
      }
    }
    if (ch === '"') {
      let k = i - 1;
      let slashes = 0;
      while (k >= 0 && src[k] === "\\") {
        slashes += 1;
        k -= 1;
      }
      if (slashes % 2 === 0) inStr = !inStr;
    }
    out += ch;
  }
  return out;
}

/** A W3C trace id (32 hex) from a uuid-like id; null when it has too few hex digits. */
export const traceHexOf = (id) => {
  const h = String(id ?? "")
    .toLowerCase()
    .replace(/[^0-9a-f]/g, "");
  return h.length >= 32 ? h.slice(0, 32) : null;
};

/** `00-<trace id>-<span id>-01`, or null on a malformed id. */
export function traceparentOf({ traceId, spanId } = {}) {
  const t = traceHexOf(traceId);
  const s = String(spanId ?? "").toLowerCase();
  if (!t || !/^[0-9a-f]{16}$/.test(s) || /^0+$/.test(t) || /^0+$/.test(s))
    return null;
  return `00-${t}-${s}-01`;
}

/** `{traceId, spanId}` of a traceparent value, or null. */
export function parseTraceparent(value) {
  const m = /^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$/.exec(
    String(value ?? "").trim(),
  );
  return m ? { traceId: m[1], spanId: m[2] } : null;
}

/** Header refs of a card's header rows (`{name, secret_ref}` | `{name, value}`). */
export function headerRefsOf(headers) {
  return (headers ?? [])
    .map((h) => ({ ...h, name: String(h?.name ?? "").trim() }))
    .filter((h) => h.name)
    .map((h) =>
      h.secret || h.secret_ref
        ? { name: h.name, secret_ref: String(h.secret ?? h.secret_ref) }
        : { name: h.name, value: String(h.value ?? "") },
    );
}

/**
 * The request one try sends.
 * @param {{endpoint: {url, method?, headers?, body_template?, bodyTemplate?},
 *   mapping?: object, values?: object, columns?: {id, name}[],
 *   rowId?: string, trial?: number, traceContext?: {traceId, spanId}}} p
 * @returns {{method, url, headers: object[], body: string, trial: number,
 *   traceparent: string|null, missing: string[], empty: string[]}}
 */
export function buildRequest({
  endpoint = {},
  mapping,
  values,
  columns,
  rowId,
  trial = 1,
  traceContext,
} = {}) {
  const method = String(endpoint.method || "POST").toUpperCase();
  const ctx = {
    mapping: mapping ?? endpoint.mapping ?? {},
    values: values ?? {},
    columns: columns ?? [],
    rowId,
  };
  const missing = [];
  const empty = [];
  const resolve = (name) => {
    const v = variableValue(name, ctx);
    if (v === undefined) {
      if (!missing.includes(name)) missing.push(name);
      return undefined;
    }
    if (!textOf(v).trim() && !empty.includes(name)) empty.push(name);
    return v;
  };
  const url = String(endpoint.url ?? "")
    .trim()
    .replace(VAR_RE, (m, name) => {
      const v = resolve(name);
      return v === undefined ? m : encodeURIComponent(textOf(v));
    });
  const template = endpoint.body_template ?? endpoint.bodyTemplate ?? "";
  let body = "";
  if (method !== "GET")
    body = isJsonTemplate(template)
      ? fillJson(template, resolve)
      : String(template).replace(VAR_RE, (m, name) => {
          const v = resolve(name);
          return v === undefined ? m : textOf(v);
        });
  const traceparent = traceContext ? traceparentOf(traceContext) : null;
  const headers = [
    ...headerRefsOf(endpoint.headers),
    ...(traceparent ? [{ name: "traceparent", value: traceparent }] : []),
  ];
  return {
    method,
    url,
    headers,
    body,
    trial: Math.max(1, Number(trial) || 1),
    traceparent,
    missing,
    empty,
  };
}

/** The headers a browser sends: literal values only (it never holds a secret). */
export function browserHeaders(req) {
  const out = {};
  (req?.headers ?? []).forEach((h) => {
    if (!h.secret_ref) out[h.name] = h.value ?? "";
  });
  return out;
}

/** Secret header names of a request (`["X-Api-Key"]`). */
export const secretHeaderNames = (req) =>
  (req?.headers ?? []).filter((h) => h.secret_ref).map((h) => h.name);

/**
 * Header lines for display. `from: "server"`: secrets read
 * `•••• (from secret NAME)`; `from: "browser"`: they read as not sent.
 */
export function headerLines(req, { from = "server" } = {}) {
  const out = {};
  (req?.headers ?? []).forEach((h) => {
    if (!h.secret_ref) out[h.name] = h.value ?? "";
    else
      out[h.name] =
        from === "browser"
          ? `not sent (from secret ${h.secret_ref})`
          : `${SECRET_MASK} (from secret ${h.secret_ref})`;
  });
  return out;
}

/** The browser Test's note on the secret headers it left out (or null). */
export function browserSecretNote(req) {
  const names = secretHeaderNames(req);
  if (!names.length) return null;
  return `Your browser sent this without ${names.join(", ")}: secret headers are added when Future AGI calls your endpoint.`;
}

/**
 * An MCP server's env secrets `[{name, secret}]`, trimmed (the older single
 * `env_secret` reads as one; rows with neither part are dropped). The run,
 * the refusal and Review read this one list.
 */
export function mcpEnvSecrets(server = {}) {
  const legacy = String(server?.env_secret ?? "").trim();
  const rows = Array.isArray(server?.env)
    ? server.env
    : legacy
      ? [{ name: legacy, secret: legacy }]
      : [];
  return rows
    .map((e) => ({
      name: String(e?.name ?? "").trim(),
      secret: String(e?.secret ?? "").trim(),
    }))
    .filter((e) => e.name || e.secret);
}

/** Secret names a request references that the workspace does not have. */
export function missingSecrets(refs, workspaceNames) {
  const have = new Set(workspaceNames ?? []);
  const out = [];
  (refs ?? []).forEach((r) => {
    const name = typeof r === "string" ? r : r?.secret_ref;
    if (name && !have.has(name) && !out.includes(name)) out.push(name);
  });
  return out;
}

/** `No workspace secret named X. Pick one from the list.` (or null). */
export function missingSecretMessage(names) {
  const list = (names ?? []).filter(Boolean);
  if (!list.length) return null;
  return list.length === 1
    ? `No workspace secret named ${list[0]}. Pick one from the list.`
    : `No workspace secrets named ${list.join(", ")}. Pick them from the list.`;
}

/**
 * The key the simulated host seeds its answer with: what was sent and the
 * try number, never a run id (so Test = try 1, and tries differ by trial).
 */
export const requestKey = (req) =>
  [req?.method, req?.url, req?.body ?? "", `try ${req?.trial ?? 1}`].join("\n");
