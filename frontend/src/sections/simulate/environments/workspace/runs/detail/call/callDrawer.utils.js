// Calls whose outcome carries no verdict on the agent.
export const UNMEASURED_LABEL = { unmeasured: "Inconclusive", error: "Error" };

export const isUnmeasured = (status) =>
  Object.prototype.hasOwnProperty.call(UNMEASURED_LABEL, status);

const asText = (value) =>
  typeof value === "string" ? value : JSON.stringify(value);

// One shape for both sources a call's tools arrive in: the v3
// `function_calls` rows (name, arguments, result, duration_ms) and chat
// messages' OpenAI-style `tool_calls` ({ function: { name, arguments } }).
function normalizeToolCall(call, index) {
  const fn = call?.function;
  const name = call?.name || fn?.name || (typeof call === "string" && call);
  const args = call?.arguments ?? fn?.arguments;
  const result = call?.result ?? call?.output;
  const ms = call?.duration_ms ?? call?.durationMs;
  return {
    key: call?.id || `tool-call-${index}`,
    name: name || "tool",
    args: args == null ? "" : asText(args),
    result: result == null ? null : asText(result),
    ms: typeof ms === "number" ? Math.round(ms) : null,
  };
}

/**
 * Every tool call in a call, in transcript order, read off the mapped turns.
 * @param {Array<{ toolCalls: ?Array }>} turns  `CallDetail.turns`.
 */
export function toolCallsOf(turns = []) {
  return turns
    .flatMap((turn) => (Array.isArray(turn.toolCalls) ? turn.toolCalls : []))
    .map(normalizeToolCall);
}
