// The builder asks to confirm a tool call as one string: `Confirm this change: <tool> <JSON args>`.
const CONFIRM_CHANGE =
  /^Confirm this change:\s*([a-z][\w]*)\s*(\{[\s\S]*\})\s*$/i;

const sentenceCase = (key) => {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
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
    title: sentenceCase(match[1]),
    fields: Object.entries(args).map(([key, value]) => ({
      key,
      label: sentenceCase(key),
      value: typeof value === "string" ? value : JSON.stringify(value, null, 2),
    })),
  };
}
