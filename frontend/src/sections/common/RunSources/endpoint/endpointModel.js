// HTTP endpoint run model (R4 lane Rb MUST 3). Pure: the default body
// template, filling `{{column}}` from a row, reading the reply with a small
// JSONPath (`$.reply`, `$.data.text`, `$.choices[0].message.content`), the
// summary lines and the WebSocket streaming template. The example server in
// examples/experiments/r4/endpoint-http/ answers exactly this template.
// W6 B1: the request itself is built by requestModel.buildRequest (one
// builder for Test and run); the header helpers here read through it.
import {
  hostAnsweringEvent,
  standInTip,
} from "src/sections/common/Connect/connectCopy";
import { browserHeaders, headerRefsOf } from "./requestModel";

/** `{"message": "{{instruction}}"}` for the dataset's input column. */
export function defaultBodyTemplate(inputColumn = "input") {
  return `{\n  "message": "{{${inputColumn}}}"\n}`;
}
export const DEFAULT_OUTPUT_PATH = "$.reply";

const jsonText = (v) => {
  if (v == null) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};
const escapeJson = (s) => JSON.stringify(String(s)).slice(1, -1);

/**
 * Fill `{{name}}` with the row's values (by column name; `row_id` too).
 * Inside a JSON body the value is escaped as a JSON string.
 * @param {string} template
 * @param {{[name: string]: any}} values
 */
export function fillTemplate(template, values) {
  return String(template ?? "").replace(
    /\{\{\s*([\w.-]+)\s*\}\}/g,
    (m, name) =>
      name in (values ?? {}) ? escapeJson(jsonText(values[name])) : m,
  );
}

/** Variables the template uses that the row lacks. */
export function missingVariables(template, values) {
  const out = [];
  String(template ?? "").replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (m, name) => {
    if (!(name in (values ?? {})) && !out.includes(name)) out.push(name);
    return m;
  });
  return out;
}

/**
 * `{{name}}` variables of a template that are not columns of the dataset
 * (N6: `{{customer_message}} is not a column of this dataset`). `row_id` is
 * always known. Empty when the columns are not known yet.
 * @param {string} template
 * @param {string[]} columnNames
 */
export function unknownColumns(template, columnNames) {
  const names = new Set((columnNames ?? []).filter(Boolean));
  if (!names.size) return [];
  names.add("row_id");
  const out = [];
  String(template ?? "").replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (m, name) => {
    if (!names.has(name) && !out.includes(name)) out.push(name);
    return m;
  });
  return out;
}

/** A small JSONPath: `$`, `.key`, `[n]`. Undefined when the path misses. */
export function pickPath(data, path) {
  const p = String(path ?? "$").trim();
  if (!p.startsWith("$")) return undefined;
  const parts = p.slice(1).match(/\.[^.[\]]+|\[\d+\]/g) ?? [];
  return parts.reduce((acc, part) => {
    if (acc == null) return undefined;
    if (part.startsWith("[")) return acc[Number(part.slice(1, -1))];
    return acc[part.slice(1)];
  }, data);
}

/** Row values by column name from an experiment sample row. */
export function rowValues(row, columns) {
  if (!row) return {};
  const out = { row_id: row.row_id ?? row.rowId ?? row.id ?? "" };
  (columns ?? []).forEach((c) => {
    const cell = row[c.id] ?? row.cells?.[c.id];
    const v =
      cell &&
      typeof cell === "object" &&
      ("cellValue" in cell || "cell_value" in cell)
        ? cell.cellValue ?? cell.cell_value
        : cell;
    if (v !== undefined) out[c.name] = v;
  });
  return out;
}

/** The collapsed `Request` summary: `POST · 2 headers · body uses instruction · reply at $.reply`. */
export function requestSummary({ method, headers, template, outputPath }) {
  const vars = [];
  String(template ?? "").replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (m, n) => {
    if (n !== "row_id" && !vars.includes(n)) vars.push(n);
    return m;
  });
  const h = (headers ?? []).filter((x) => x.name).length;
  return [
    method || "POST",
    h ? `${h} header${h === 1 ? "" : "s"} from secrets` : "no headers",
    vars.length ? `body uses ${vars.join(", ")}` : "no variables",
    `reply at ${outputPath || DEFAULT_OUTPUT_PATH}`,
  ].join(" · ");
}

export const AUDIO_MODES = Object.freeze([
  { id: "text", label: "Text in, text out" },
  { id: "audio_in", label: "Audio in (multipart)" },
  { id: "audio_out", label: "Audio out (response audio URL)" },
]);

/**
 * The audio modes the endpoint form offers (options-truth L5 G5; W6 B1):
 * audio out, WebSocket streaming and audio in (multipart) stay out of the
 * form until a run builds their request body. Only text is built
 * (requestModel.buildRequest), so the form shows no Audio choice.
 */
export const OFFERED_AUDIO_MODES = Object.freeze(
  AUDIO_MODES.filter((m) => m.id === "text"),
);

/** `Text in, text out` / `Audio in (multipart) · streamed` (summary line). */
export function streamingSummary({ audio, streaming }) {
  const a =
    AUDIO_MODES.find((m) => m.id === audio)?.label ?? AUDIO_MODES[0].label;
  return streaming ? `${a} · streamed over WebSocket` : a;
}

/** The WebSocket streaming template (one message in, chunks out, `done`). */
export function websocketTemplate(inputColumn = "input") {
  return [
    "// send once per row",
    `{"type": "input", "row_id": "{{row_id}}", "text": "{{${inputColumn}}}"}`,
    "// your server streams chunks, then one done message",
    '{"type": "chunk", "text": "…"}',
    '{"type": "done", "reply": "the full reply"}',
  ].join("\n");
}

// Hosts Future AGI's servers cannot reach: localhost and private networks
// (the card's URL hint, stepIssues.httpUrlHint, says the same words).
const PRIVATE_HOST =
  /^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$|\.local$|\.internal$/i;
export const UNREACHABLE_TEXT =
  "Future AGI's servers cannot reach localhost or a private network. Use a tunnel (https).";

/** True when a URL's host is localhost or on a private network. */
export function isPrivateHostUrl(url) {
  const raw = String(url ?? "").trim();
  if (!raw) return false;
  try {
    const u = new URL(/^[a-z]+:\/\//i.test(raw) ? raw : `https://${raw}`);
    return PRIVATE_HOST.test(u.hostname);
  } catch {
    return false;
  }
}

/**
 * Why Future AGI cannot call a URL (`Couldn't reach 10.20.30.40: …`), or
 * null: the run's tries, Test, Discover and Connect all refuse such a host.
 */
export function unreachableReason(url) {
  if (!isPrivateHostUrl(url)) return null;
  let host = String(url ?? "").trim();
  try {
    host = new URL(/^[a-z]+:\/\//i.test(host) ? host : `https://${host}`).host;
  } catch {
    /* keep the raw text */
  }
  return `Couldn't reach ${host}: ${UNREACHABLE_TEXT}`;
}

/** True for a URL only your machine can reach (the browser calls it). */
export const isLocalUrl = (url) =>
  /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i.test(
    String(url ?? "").trim(),
  );

/**
 * The request headers a browser test sends (options-truth L5 G5, W0-6):
 * only headers with a literal value. A header from a workspace secret is
 * not sent from the browser (the page never holds a secret's value), and
 * nothing stands in for it.
 * @param {{name: string, secret?: string, value?: string}[]} headers
 */
export function testHeaders(headers) {
  return browserHeaders({ headers: headerRefsOf(headers) });
}

/**
 * The headers of a test for display: what was sent, and each secret header
 * by its secret's name, marked as not sent (pure).
 */
export const shownHeaders = (headers) => {
  const out = { ...testHeaders(headers) };
  (headers ?? []).forEach((h) => {
    const name = String(h?.name ?? "").trim();
    if (name && h.secret)
      out[name] = `from secret ${h.secret} (not sent from the browser)`;
  });
  return out;
};

/**
 * L7c g2 (action:http|a2a|mcp:test-on-one-row, STANDIN): the one tooltip of
 * the `Simulated` tag on a Test result Future AGI's simulated host answered
 * (the one stand-in wording: `Stands in for <who>.`): `Stands in for
 * api.acme.ai answering.` A browser call to localhost is real and a refused
 * host answers nothing, so neither carries the tag.
 */
export const standInAnswerTip = (who) => standInTip(hostAnsweringEvent(who));
