// ONE command builder (options-truth L5 G5, extra:one-command-builder). Every
// command a screen prints is built here from the request that screen sends:
// `{method, url, headers, body}` → `{curl, python, typescript}`. The body is
// printed exactly as sent (same keys, same values), and every Future AGI
// request carries the same two auth headers, read from the environment.
// Pure; no network.
import { FI_BASE_URL } from "./apiBase";

/**
 * The Future AGI API host printed in commands (uploadSnippets re-exports):
 * the capability contract(4), this build's HOST_API, never a fixed production host.
 */
export { FI_BASE_URL };

/** Header name → environment variable it is read from. */
export const FI_AUTH_HEADERS = Object.freeze({
  "X-Api-Key": "FI_API_KEY",
  "X-Secret-Key": "FI_SECRET_KEY",
});

/**
 * The request a screen sends to a Future AGI API path, with the auth
 * headers every printed command uses.
 * @param {string} path  e.g. `/model-hub/datasets/imports/`
 * @param {object} body  the JSON body exactly as the screen sends it
 * @param {{method?: string}} [opts]
 */
export function fiRequest(path, body, { method = "POST" } = {}) {
  return {
    method,
    url: `${FI_BASE_URL}${path}`,
    headers: { ...FI_AUTH_HEADERS, "Content-Type": "application/json" },
    body,
  };
}

/**
 * The GET request a screen sends: `path?query`, the query in the given key
 * order (empty values left out), with the same auth headers. the interface contract: R3 and
 * get-dataset-table are GETs (Copy as API call for a filter).
 * @param {string} path  e.g. `/model-hub/datasets/{id}/results/`
 * @param {Object<string, string|number|boolean>} params
 */
export function fiGetRequest(path, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    query.append(k, typeof v === "string" ? v : String(v));
  });
  const qs = query.toString();
  return {
    method: "GET",
    url: `${FI_BASE_URL}${path}${qs ? `?${qs}` : ""}`,
    headers: { ...FI_AUTH_HEADERS },
  };
}

const pad = (n) => "    ".repeat(n);

/** A JSON value as a Python literal (True / False / None, quoted keys). */
export function toPythonLiteral(value, depth = 0) {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "number")
    return Number.isFinite(value) ? String(value) : "None";
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    return `[\n${value
      .map((v) => `${pad(depth + 1)}${toPythonLiteral(v, depth + 1)}`)
      .join(",\n")},\n${pad(depth)}]`;
  }
  const entries = Object.entries(value).filter(([, v]) => v !== undefined);
  if (!entries.length) return "{}";
  return `{\n${entries
    .map(
      ([k, v]) =>
        `${pad(depth + 1)}${JSON.stringify(k)}: ${toPythonLiteral(v, depth + 1)}`,
    )
    .join(",\n")},\n${pad(depth)}}`;
}

// A header value is either an env var reference (from FI_AUTH_HEADERS) or a
// literal (Content-Type).
const envOf = (value) =>
  Object.values(FI_AUTH_HEADERS).includes(value) ? value : null;

const shellQuote = (s) => `'${String(s).replace(/'/g, "'\\''")}'`;

/**
 * `{method, url, headers, body}` → `{curl, python, typescript}`.
 * Shared by public API copy actions. Each snippet starts with a Keys-page
 * comment and authenticates only with FI_API_KEY and FI_SECRET_KEY from the
 * environment. Caller credential/session headers are never printed; other
 * headers, the URL and the body are preserved. The input and return fields
 * remain stable so every screen uses this builder rather than its own auth.
 */
export function requestSnippets({ method = "POST", url, headers = {}, body }) {
  const verb = String(method).toUpperCase();
  const hasBody = body !== undefined && verb !== "GET";
  const json = hasBody ? JSON.stringify(body, null, 2) : null;
  const entries = [
    ...Object.entries(FI_AUTH_HEADERS),
    ...Object.entries(headers).filter(
      ([name]) =>
        !/^(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-secret-key)$/i.test(
          name,
        ),
    ),
  ];
  const keysHint = "Create a personal API key pair at /dashboard/keys";

  const curl =
    `# ${keysHint}\n` +
    [
      `curl -X ${verb} "${url}"`,
      ...entries.map(([k, v]) =>
        envOf(v) ? `-H "${k}: $${v}"` : `-H ${shellQuote(`${k}: ${v}`)}`,
      ),
      ...(hasBody ? [`-d ${shellQuote(json)}`] : []),
    ].join(" \\\n  ");

  const pyHeaders = entries
    .filter(([k]) => k.toLowerCase() !== "content-type" || !hasBody)
    .map(([k, v]) =>
      envOf(v)
        ? `${pad(2)}${JSON.stringify(k)}: os.environ[${JSON.stringify(v)}],`
        : `${pad(2)}${JSON.stringify(k)}: ${JSON.stringify(v)},`,
    );
  const python = [
    `# ${keysHint}`,
    "import os",
    "import requests",
    "",
    ...(hasBody ? [`body = ${toPythonLiteral(body)}`, ""] : []),
    `res = requests.request(`,
    `${pad(1)}${JSON.stringify(verb)},`,
    `${pad(1)}${JSON.stringify(url)},`,
    `${pad(1)}headers={`,
    ...pyHeaders,
    `${pad(1)}},`,
    ...(hasBody ? [`${pad(1)}json=body,`] : []),
    ")",
    "res.raise_for_status()",
    'print(res.json()["result"])',
  ].join("\n");

  const tsHeaders = entries.map(([k, v]) =>
    envOf(v)
      ? `    ${JSON.stringify(k)}: process.env.${v}!,`
      : `    ${JSON.stringify(k)}: ${JSON.stringify(v)},`,
  );
  const typescript = [
    `// ${keysHint}`,
    ...(hasBody ? [`const body = ${json};`, ""] : []),
    `const res = await fetch(${JSON.stringify(url)}, {`,
    `  method: ${JSON.stringify(verb)},`,
    "  headers: {",
    ...tsHeaders,
    "  },",
    ...(hasBody ? ["  body: JSON.stringify(body),"] : []),
    "});",
    "if (!res.ok) throw new Error(`HTTP ${res.status}`);",
    "console.log((await res.json()).result);",
  ].join("\n");

  return { curl, python, typescript };
}
