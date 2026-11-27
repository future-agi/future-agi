export const sentence = (text) =>
  text ? text.charAt(0).toUpperCase() + text.slice(1) : "";

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * Group one-off issues by their name. Each group's count is its unique calls,
 * the same calls its View link opens, and the most-seen group comes first.
 */
export function groupOneOffs(oneOffs) {
  const groups = new Map();
  oneOffs.forEach((way) => {
    const phrase = sentence(way.phrase || way.title || "Other");
    const key = phrase.toLowerCase();
    if (!groups.has(key)) groups.set(key, { phrase, ways: [] });
    groups.get(key).ways.push(way);
  });
  return [...groups.values()]
    .map((group) => ({
      ...group,
      callIds: [...new Set(group.ways.flatMap((way) => way.callIds ?? []))],
    }))
    .sort((a, b) => b.callIds.length - a.callIds.length);
}

// A cluster title as the backend names it (_phrase in simulation_diagnosis.py):
// no leading "the agent" / "voice assistant" subject, no trailing full stop.
const SUBJECT = /^(the )?(voice )?(assistant|agent)('s)?\s+/i;
const phraseOf = (text) =>
  String(text ?? "")
    .trim()
    .replace(SUBJECT, "")
    .replace(/\.+$/, "")
    .split(/\s+/)
    .join(" ")
    .toLowerCase();

// Whether a way's title just says its group's name again.
export const sameName = (title, name) => phraseOf(title) === phraseOf(name);
