const parseJson = (value) => {
  if (value == null || value === "") return undefined;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
};

const messagesOf = (source) => {
  const value = parseJson(source);
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.messages)) return value.messages;
  if (Array.isArray(value?.turns)) return value.turns;
  return [];
};

const callParts = (call) => call?.function ?? call ?? {};

const argsOf = (call) => {
  const parts = callParts(call);
  const raw = parts.arguments ?? call?.args ?? call?.arguments ?? {};
  return parseJson(raw) ?? {};
};

const toolMessageFor = (call, messages) => {
  const list = messagesOf(messages);
  if (!list.length) return null;
  const id = call?.id ?? call?.tool_call_id;
  if (id) {
    const matching = list.find(
      (message) =>
        message?.role === "tool" &&
        (message.tool_call_id === id || message.id === id),
    );
    if (matching) return matching;
  }
  // No id match: the first tool message after the message that made the call.
  const same = (other) =>
    other === call ||
    (id && other?.id === id) ||
    JSON.stringify(other) === JSON.stringify(call);
  const owner = list.findIndex((message) =>
    (message?.tool_calls ?? message?.toolCalls ?? []).some(same),
  );
  return (
    list.slice(owner + 1).find((message) => message?.role === "tool") ?? null
  );
};

/** Normalize OpenAI calls and the compact `{name, args, result}` fixture shape. */
export function normalizeToolCall(call, { messages } = {}) {
  const parts = callParts(call);
  const result =
    call?.result !== undefined
      ? call.result
      : toolMessageFor(call, messages)?.content;
  return {
    ...call,
    id: call?.id ?? call?.tool_call_id ?? null,
    name: parts.name ?? call?.name ?? "tool",
    args: argsOf(call),
    result,
    status: call?.status,
    status_code: call?.status_code,
  };
}

export function toolCallsForMessage(message, messages) {
  return (message?.tool_calls ?? message?.toolCalls ?? []).map((call) =>
    normalizeToolCall(call, { messages }),
  );
}

const quote = (value) => {
  if (typeof value === "string") return JSON.stringify(value);
  if (value == null) return "null";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
};

export function toolCallText(call, options) {
  const normalized = normalizeToolCall(call, options);
  const args = normalized.args;
  const argList =
    args && typeof args === "object" && !Array.isArray(args)
      ? Object.entries(args)
          .map(([key, value]) => `${key}: ${quote(value)}`)
          .join(", ")
      : "";
  return `${normalized.name}(${argList}) → ${toolOutcome(normalized).label}`;
}

export function toolNameOf(call, options) {
  return normalizeToolCall(call, options).name;
}

export function toolOutcome(call, options) {
  const normalized = normalizeToolCall(call, options);
  const result = parseJson(normalized.result);
  const error = result && typeof result === "object" ? result.error : null;
  const explicitCode =
    result && typeof result === "object"
      ? result.status_code ?? result.code
      : undefined;
  const code =
    Number(error?.code) ||
    Number(explicitCode) ||
    Number(normalized.status_code) ||
    (normalized.status === "error" || error ? null : 200);
  const failed =
    normalized.status === "error" ||
    Boolean(error) ||
    (code != null && code >= 400);
  const statusText =
    typeof result?.status === "string" &&
    !["ok", "found", "success", "passed"].includes(result.status.toLowerCase())
      ? result.status
      : "OK";
  return {
    code: code || null,
    tone: failed ? "error" : "ok",
    label: code
      ? `${code} ${failed ? "Error" : statusText.toUpperCase()}`
      : failed
        ? "Error"
        : "OK",
    message: typeof error?.message === "string" ? error.message : null,
  };
}

export { messagesOf, parseJson };
