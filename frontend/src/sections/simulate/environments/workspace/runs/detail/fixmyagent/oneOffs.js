const sentence = (text) =>
  text ? text.charAt(0).toUpperCase() + text.slice(1) : "";

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
