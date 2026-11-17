/** Count conversational turns, excluding system and tool messages. */
export function countTurns(messages) {
  return (Array.isArray(messages) ? messages : []).filter((message) => {
    const role = String(message?.role ?? "").toLowerCase();
    return [
      "user",
      "assistant",
      "customer",
      "agent",
      "human",
      "ai",
      "bot",
      "caller",
      "model",
    ].includes(role);
  }).length;
}

export default countTurns;
