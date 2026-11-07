/** Native OTel GenAI messages use `parts[].content`, not OpenAI `content`.
 * Normalize only the display value; keep stored columns/attributes unchanged.
 * Unknown parts (including tools) stay visible as JSON rather than disappearing.
 */
export function normalizeNativeGenAIMessage(message) {
  if (!message || !Array.isArray(message.parts) || message.content != null) {
    return message;
  }
  return {
    ...message,
    content: message.parts.map((part) => {
      if (typeof part === "string") return part;
      if (typeof part?.text === "string") return part.text;
      if (part?.type === "text" && typeof part.content === "string") {
        return part.content;
      }
      return JSON.stringify(part);
    }),
  };
}

export function parseNativeGenAIMessages(value) {
  let data = value;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch {
      return [];
    }
  }
  if (
    !Array.isArray(data) ||
    !data.every(
      (message) =>
        typeof message?.role === "string" && Array.isArray(message.parts),
    )
  ) {
    return [];
  }
  return data.map(normalizeNativeGenAIMessage);
}
