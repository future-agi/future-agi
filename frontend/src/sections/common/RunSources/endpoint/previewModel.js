// The per-row request preview of an HTTP card (W6 B2). Pure: one row's
// request through the one builder, and the variables that are empty or
// missing across the rows the form has loaded.
import { buildRequest, headerLines, valuesById } from "./requestModel";

/** The card's form fields as the run's endpoint. */
export const endpointOfCard = (http = {}) => ({
  url: http.url ?? "",
  method: http.method ?? "POST",
  headers: http.headers ?? [],
  body_template: http.bodyTemplate ?? http.body_template ?? "",
  output_path: http.outputPath ?? http.output_path ?? "",
});

/** What the run's try 1 sends for one row (secrets shown by reference). */
export function previewOf({ http, mapping, columns, row }) {
  const req = buildRequest({
    endpoint: endpointOfCard(http),
    mapping: mapping ?? {},
    values: valuesById(row, columns ?? []),
    columns: columns ?? [],
    rowId: row?.row_id ?? null,
    trial: 1,
  });
  return { ...req, shownHeaders: headerLines(req) };
}

/**
 * Variables that are empty (or that nothing supplies) on the loaded rows:
 * `[{name, rows: [rowRef…], kind: "empty"|"missing"}]`, most rows first.
 */
export function emptyVariables({ http, mapping, columns, rows }) {
  const by = new Map();
  (rows ?? []).forEach((row) => {
    const p = previewOf({ http, mapping, columns, row });
    const add = (name, kind) => {
      const k = `${kind}|${name}`;
      if (!by.has(k)) by.set(k, { name, kind, rows: [] });
      by.get(k).rows.push(row);
    };
    p.empty.forEach((n) => add(n, "empty"));
    p.missing.forEach((n) => add(n, "missing"));
  });
  return [...by.values()].sort(
    (a, b) => b.rows.length - a.rows.length || a.name.localeCompare(b.name),
  );
}

/** `{{intent}} is empty on 3 of 20 rows` (the first rows when more exist). */
export function emptyLine(v, loaded, total) {
  const of = total && total > loaded ? `the first ${loaded}` : `${loaded}`;
  return v.kind === "missing"
    ? `{{${v.name}}} has no column or mapping: it is sent as written on ${v.rows.length} of ${of} rows`
    : `{{${v.name}}} is empty on ${v.rows.length} of ${of} rows`;
}

/**
 * L7c g2 (knob:http:output-path): the JSONPaths of the text values in a
 * response (`$.data.answer`, `$.actions[0].name`), so Test can offer them
 * as the output path. Pure; at most `limit`, breadth first.
 * @param {unknown} json
 * @param {{limit?: number}} [o]
 * @returns {string[]}
 */
export function textPathsOf(json, { limit = 6 } = {}) {
  const out = [];
  const queue = [["$", json]];
  while (queue.length && out.length < limit) {
    const [path, v] = queue.shift();
    if (typeof v === "string") {
      // ids (`request_id`) are not replies.
      if (v.trim() && !/(^|[._])id"?\]?$/i.test(path)) out.push(path);
    } else if (Array.isArray(v)) {
      v.slice(0, 3).forEach((x, i) => queue.push([`${path}[${i}]`, x]));
    } else if (v && typeof v === "object") {
      Object.entries(v).forEach(([k, x]) =>
        queue.push([
          /^[A-Za-z_]\w*$/.test(k) ? `${path}.${k}` : `${path}["${k}"]`,
          x,
        ]),
      );
    }
  }
  return out;
}
