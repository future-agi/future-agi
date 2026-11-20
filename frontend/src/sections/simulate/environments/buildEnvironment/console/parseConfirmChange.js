// The builder asks to confirm a tool call as one string: `Confirm this change: <tool> <JSON args>`.
const CONFIRM_CHANGE =
  /^Confirm this change:\s*([a-z][\w]*)\s*(\{[\s\S]*\})\s*$/i;
const IDENTIFIER = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/;
const ACRONYMS = new Set(["ai", "api", "id", "llm", "url"]);

// `agent_response` -> "Agent response", `llm` -> "LLM".
const humanize = (identifier) => {
  const words = identifier
    .split("_")
    .filter(Boolean)
    .map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w))
    .join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const readable = (value) => {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  return IDENTIFIER.test(value) ? humanize(value) : value;
};

// Null when the prompt is anything else, so the caller shows it as written.
export function parseConfirmChange(text = "") {
  const match = CONFIRM_CHANGE.exec(text);
  if (!match) return null;
  let args;
  try {
    args = JSON.parse(match[2]);
  } catch {
    return null;
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) return null;
  return {
    title: humanize(match[1]),
    fields: Object.entries(args).map(([key, value]) => ({
      key,
      label: humanize(key),
      value: readable(value),
    })),
  };
}
