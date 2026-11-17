// L7c g2: the pure parts of the endpoint, A2A and MCP cards (names from
// their config, the tries limit), kept out of the .jsx cards.

/** `Acme Support Agent 1.2 · skill refund_request` (pure). */
export const a2aIdentity = (card, skillId) =>
  card
    ? `${card.name} ${card.version}${skillId ? ` · skill ${skillId}` : ""}`
    : "";

/**
 * L7c g2 (source:running:a2a): the run's name from its card, the one name
 * Review, the footer and the created run show: `A2A · Globex Support Agent
 * 1.4 · skill refund_request`; the generic name until a card is discovered.
 */
export const a2aRunName = (card, skillId) =>
  card ? `A2A · ${a2aIdentity(card, skillId)}` : "A2A agent card";

/** True for a name the card set itself (generic or from a card). */
export const isAutoA2aName = (name) =>
  !String(name ?? "").trim() ||
  name === "A2A agent card" ||
  /^A2A · /.test(String(name));

/**
 * `env ACME_SANDBOX_TOKEN ← secret FI_PROJECT_KEY` for an MCP server's env
 * secrets `[{name, secret}]` (requestModel.mcpEnvSecrets), the words Run
 * details' SOURCE line uses; null when none is complete (pure).
 */
export const mcpEnvLine = (env) => {
  const rows = (env ?? []).filter((x) => x?.name && x?.secret);
  return rows.length
    ? `env ${rows.map((x) => `${x.name} ← secret ${x.secret}`).join(", ")}`
    : null;
};

/**
 * L7c g2 (source:running:mcp_tool, knob:mcp:env-secret): the run's name
 * from its server, tool and env secrets, the one name Review, the footer
 * and the created run show: `MCP · acme-support-sandbox · lookup_order ·
 * env ACME_SANDBOX_TOKEN ← secret FI_PROJECT_KEY`.
 */
export const mcpRunName = (serverName, tool, env) =>
  serverName && tool
    ? [`MCP · ${serverName} · ${tool}`, mcpEnvLine(env)]
        .filter(Boolean)
        .join(" · ")
    : "MCP server tool";

/** True for a name the card set itself (generic or from a server). */
export const isAutoMcpName = (name) =>
  !String(name ?? "").trim() ||
  name === "MCP server tool" ||
  /^MCP · /.test(String(name));

// The most tries a run plays per row (r4-sources-runs sourceRecords).
export const MAX_TRIES_PER_ROW = 10;

/** A positive number from a field (`int`: rounded, at least 1), or null. */
export function toPositive(v, { int = false } = {}) {
  if (v === "" || v == null) return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  return int ? Math.max(1, Math.round(n)) : n;
}

/**
 * L7c g2 (knob:limits:tries): a typed tries value the run would not play
 * is refused (pure): `{value, refused}`; above the engine's 10 the value
 * stays at 10 and `refused` names what was typed.
 */
export function clampTries(raw) {
  const n = toPositive(raw, { int: true });
  if (n == null) return { value: null, refused: null };
  if (n > MAX_TRIES_PER_ROW) return { value: MAX_TRIES_PER_ROW, refused: n };
  return { value: n, refused: null };
}

// ------------------------------------------------------------ HTTP method

const VARS_RE = /{{\s*([^{}]+?)\s*}}/g;
const varsOf = (text) => {
  const out = [];
  String(text ?? "").replace(VARS_RE, (m, name) => {
    if (!out.includes(name)) out.push(name);
    return m;
  });
  return out;
};

/** True for a GET (the method field's value, any case). */
export const isGetMethod = (method) =>
  String(method ?? "").toUpperCase() === "GET";

/**
 * L7c g2 (knob:http:method): the text whose `{{variables}}` a request
 * sends: a GET sends no body, so only its URL counts (pure).
 */
export const sentTemplateOf = ({ method, url, body }) =>
  isGetMethod(method) ? String(url ?? "") : `${url ?? ""}\n${body ?? ""}`;

/** The GET note under the URL, with the dataset's input column (pure). */
export const getBodyNote = (inputColumn) =>
  `GET sends no body: put {{${inputColumn || "column"}}} in the URL query, e.g. ?q={{${inputColumn || "column"}}}`;

/**
 * L7c g2 (knob:http:method): what a method change does to the card (pure).
 * To GET: the body is not kept on the card (it would never be sent) and the
 * mapping of variables only the body used is dropped; both are put aside so
 * a change back restores them. From GET to a method with a body: an empty
 * body gets the put-aside one back, else the stock body for the input
 * column. Returns null when nothing changes.
 * @param {{toGet: boolean, url?: string, body?: string, mapping?: object,
 *   aside?: {body: string, mapping: object}|null, stockBody: string}} p
 * @returns {{body: string, mapping: object, aside: object|null}|null}
 */
export function httpMethodSwitch({
  toGet,
  url,
  body,
  mapping,
  aside = null,
  stockBody,
}) {
  const map = mapping ?? {};
  const text = String(body ?? "");
  if (toGet) {
    const inUrl = new Set(varsOf(url));
    const bodyOnly = varsOf(text).filter((v) => !inUrl.has(v));
    const dropped = Object.fromEntries(
      Object.entries(map).filter(([k]) => bodyOnly.includes(k)),
    );
    if (!text && !Object.keys(dropped).length) return null;
    const kept = Object.fromEntries(
      Object.entries(map).filter(([k]) => !(k in dropped)),
    );
    return {
      body: "",
      mapping: kept,
      aside: { body: text || aside?.body || "", mapping: dropped },
    };
  }
  if (text.trim()) return null;
  return {
    body: aside?.body || stockBody,
    mapping: { ...(aside?.mapping ?? {}), ...map },
    aside: null,
  };
}
