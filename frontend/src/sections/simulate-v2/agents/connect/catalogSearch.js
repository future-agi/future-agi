/*
  Search over the ways to connect an agent, forgiving of how people type:
  case, spaces and punctuation don't matter ("eleven labs", "crew ai"), every
  word typed has to appear somewhere, and a way in is found by its name, its
  next step, what it needs, its group or its keywords (LangChain finds
  LangGraph, Dialogflow finds Google CX).
*/

/** Lower-case with everything but letters and digits removed. */
export const normalize = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

const fieldsOf = (it) => ({
  name: normalize(it.name),
  rest: normalize([it.need, it.next, it.group?.title, it.keywords].filter(Boolean).join(" ")),
});

/**
 * How well a way in matches `query`: 0 when it doesn't; higher is better —
 * a name that starts with it, then a name that contains it, then any field.
 */
export const matchScore = (it, query) => {
  const whole = normalize(query);
  if (!whole) return 0;
  const { name, rest } = fieldsOf(it);
  if (name.startsWith(whole)) return 4;
  if (name.includes(whole)) return 3;
  /* Every word typed, each somewhere — "voice phone", "python langchain". */
  const words = String(query).toLowerCase().split(/\s+/).map(normalize).filter(Boolean);
  const all = `${name} ${rest}`;
  if (all.includes(whole)) return 2;
  if (words.length > 1 && words.every((w) => all.includes(w))) return 1;
  return 0;
};

/** The ways in that match, best first, keeping catalogue order within a score. */
export const searchCatalog = (items, query) => items
  .map((it, i) => ({ it, i, score: matchScore(it, query) }))
  .filter((x) => x.score > 0)
  .sort((a, b) => b.score - a.score || a.i - b.i)
  .map((x) => x.it);

/**
 * Where `query` sits in `text`, for highlighting: [start, end] or null.
 * Plain, case-insensitive — a match found only through keywords isn't marked.
 */
export const highlightOf = (text, query) => {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return null;
  const at = String(text || "").toLowerCase().indexOf(q);
  return at < 0 ? null : [at, at + q.length];
};
