// The A2A agent card's requests, as the server sends them for each row
// (futureagi/model_hub/services/http_experiment_source.py A2A_BODY_TEMPLATE,
// a2a_reply_text; serializers/experiments.py _a2a_jsonrpc_url,
// _a2a_auth_headers). Pure. Discover and Test on one row go through the
// core's endpoint test route (public hosts only, no redirects, secrets
// resolved in the workspace), so the browser never calls the agent itself.
import { authOf } from "./a2aAuthModel";

export const ENDPOINT_TEST = "/model-hub/r4/endpoints/test/";
export const AGENT_CARD_PATHS = Object.freeze([
  "/.well-known/agent-card.json",
  // A2A before 0.3 served the card here.
  "/.well-known/agent.json",
]);
/** The auth kinds the server sends for an A2A agent. */
export const A2A_AUTH_SENT = Object.freeze(["none", "bearer", "api_key"]);

const s = (v) => String(v ?? "").trim();
const list = (v) => (Array.isArray(v) ? v : []);

/**
 * Where to look for the agent card: the URL itself when it names a card
 * (`.json` or `/.well-known/`), else each well-known path on its origin.
 */
export function agentCardUrls(input) {
  const text = s(input);
  if (!text) return [];
  let url;
  try {
    url = new URL(text);
  } catch {
    return [];
  }
  if (/\.json$/i.test(url.pathname) || url.pathname.includes("/.well-known/"))
    return [url.href];
  const base = `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
  return AGENT_CARD_PATHS.map((path) => `${base}${path}`);
}

/** Whether a JSON answer reads as an A2A agent card. */
export const isAgentCard = (v) =>
  Boolean(v) &&
  typeof v === "object" &&
  !Array.isArray(v) &&
  typeof v.name === "string" &&
  (typeof v.url === "string" || Array.isArray(v.additionalInterfaces));

/** The card with the fields the form shows always present. */
export function normalizedCard(card) {
  const modes = list(card?.defaultInputModes).map(String);
  return {
    ...card,
    name: s(card?.name) || "A2A agent",
    version: s(card?.version),
    description: s(card?.description),
    skills: list(card?.skills)
      .filter((skill) => skill && typeof skill === "object" && s(skill.id))
      .map((skill) => ({
        ...skill,
        id: s(skill.id),
        name: s(skill.name) || s(skill.id),
        description: s(skill.description),
        tags: list(skill.tags).map(String),
        inputModes: list(skill.inputModes).length
          ? list(skill.inputModes).map(String)
          : modes.length
            ? modes
            : ["text"],
      })),
  };
}

/** The card's JSON-RPC URL, or null (the server refuses such a card). */
export function agentJsonRpcUrl(card) {
  const transport = s(card?.preferredTransport || "JSONRPC").toUpperCase();
  if (transport === "JSONRPC" && s(card?.url)) return s(card.url);
  const other = list(card?.additionalInterfaces).find(
    (i) => i && s(i.transport).toUpperCase() === "JSONRPC" && s(i.url),
  );
  return other ? s(other.url) : null;
}

/** Why this auth cannot be sent, in the server's words, or null. */
export function a2aAuthProblem(auth) {
  const a = authOf(auth);
  if (!A2A_AUTH_SENT.includes(a.kind))
    return "Basic and OAuth 2 sign-in for A2A agents aren't available in this release. Choose Bearer token, API key header or None.";
  if (a.kind !== "none" && !s(a.secret))
    return "Pick the workspace secret this auth sends.";
  return null;
}

/**
 * The headers an auth adds, as secret references: Bearer goes in
 * Authorization as `Bearer <secret>`, an API key in its header. Null when
 * the auth cannot be sent (a2aAuthProblem).
 */
export function a2aRequestHeaders(auth) {
  if (a2aAuthProblem(auth)) return null;
  const a = authOf(auth);
  if (a.kind === "none") return [];
  if (a.kind === "bearer")
    return [
      { name: "Authorization", secret_ref: s(a.secret), scheme: "bearer" },
    ];
  return [{ name: s(a.header) || "X-Api-Key", secret_ref: s(a.secret) }];
}

const textOf = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);

/** The JSON-RPC message/send body one row sends (the server's template). */
export function a2aMessageBody(text, id) {
  const ref = s(id) || "test-row";
  return JSON.stringify({
    jsonrpc: "2.0",
    id: ref,
    method: "message/send",
    params: {
      message: {
        kind: "message",
        role: "user",
        messageId: ref,
        parts: [{ kind: "text", text: textOf(text) }],
      },
      configuration: { blocking: true },
    },
  });
}

const WAITING = new Set([
  "submitted",
  "working",
  "input-required",
  "input_required",
  "auth-required",
  "auth_required",
]);
const FAILED = new Set(["failed", "canceled", "cancelled", "rejected"]);

const textsOf = (parts) =>
  list(parts)
    .filter(
      (p) => p && (p.kind ?? p.type) === "text" && typeof p.text === "string",
    )
    .map((p) => p.text);

/**
 * What a message/send reply says, in the server's words:
 * `{text, error, state, taskId}`. A Message gives its text parts; a Task its
 * artifacts' text, else its status message. An unfinished Task is an error.
 */
export function a2aReplyOf(payload) {
  const notReply = {
    text: null,
    error:
      "The agent's reply is not an A2A message/send result. Check the agent URL, then retry.",
    state: null,
    taskId: null,
  };
  if (!payload || typeof payload !== "object" || Array.isArray(payload))
    return notReply;
  if (payload.error != null) {
    const code =
      typeof payload.error?.code === "number" ? ` ${payload.error.code}` : "";
    return {
      ...notReply,
      error: `The agent answered with A2A error${code}. Check the agent and its settings, then Retry this row.`,
    };
  }
  const result = payload.result;
  if (!result || typeof result !== "object" || Array.isArray(result))
    return notReply;
  let texts;
  let state = null;
  const taskId = typeof result.id === "string" ? result.id : null;
  if (result.kind === "task" || "status" in result) {
    const status =
      result.status && typeof result.status === "object" ? result.status : {};
    state = typeof status.state === "string" ? status.state : null;
    if (WAITING.has(state))
      return {
        text: null,
        state,
        taskId,
        error: `The agent's task is still ${state}. Future AGI sends one message per row and does not wait. Make the agent finish before it answers, then Retry this row.`,
      };
    if (FAILED.has(state))
      return {
        text: null,
        state,
        taskId,
        error: `The agent's task ended as ${state}. Check the agent, then Retry this row.`,
      };
    if (state !== "completed")
      return {
        text: null,
        state: null,
        taskId,
        error:
          "The agent's task has no finished state. Check the agent, then Retry this row.",
      };
    // A2A artifacts are a list; anything else is a malformed reply (the
    // server's row error says the same).
    if (result.artifacts != null && !Array.isArray(result.artifacts))
      return { ...notReply, state, taskId };
    texts = list(result.artifacts).flatMap((a) => textsOf(a?.parts));
    if (!texts.length) {
      const m = status.message;
      texts = textsOf(Array.isArray(m) ? m : m?.parts);
    }
  } else {
    texts = textsOf(result.parts);
  }
  if (!texts.length)
    return {
      text: null,
      state,
      taskId,
      error:
        "The agent answered without a text part. Make it reply with text, then Retry this row.",
    };
  return { text: texts.join("\n"), error: null, state, taskId };
}
