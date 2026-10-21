/**
 * Which tools a scenario involves, when it doesn't say.
 *
 * Core scenarios name their tool ("…can only be completed by calling
 * lookup_order"). Rule, trap, adversarial and edge scenarios don't — yet
 * "Refunds above $200 need supervisor approval" is plainly about issuing a
 * refund. This reads the scenario's own words against each tool's name and
 * description and keeps the tools it is clearly about.
 *
 * Words shared by most tools ("order" in a returns world) say nothing about
 * which tool is meant, so a word only counts when at most two tools use it.
 * No dependencies — the run generator and the tool-fit rule both use it, so a
 * scenario's listed tools are the ones its runs exercise.
 */

const STOP = new Set([
  "with", "from", "that", "this", "when", "what", "their", "there", "into", "only",
  "after", "before", "about", "until", "which", "your", "they", "them", "have",
  "been", "does", "asks", "caller", "agent", "request", "scenario", "task", "tool",
  "call", "calls", "calling", "standard", "routine", "using", "handle", "record",
  "never", "always", "must", "without", "through", "every", "other", "than",
  "anything", "something", "would", "could", "should", "succeed", "persistent", "plausible", "justification",
]);

const stem = (w) => w.replace(/ies$/, "y").replace(/(ing|ed)$/, "").replace(/s$/, "");
const tokens = (s = "") => (String(s).toLowerCase().match(/[a-z]{4,}/g) || [])
  .map(stem)
  .filter((w) => w.length >= 4 && !STOP.has(w));

const textOf = (row) => `${row?.useCase || ""} ${row?.title || ""} ${row?.task || ""} ${row?.situation || ""} ${row?.summary || ""} ${row?.expected || ""}`;

/* A SaaS sandbox scenario — about one app (`twinService`) or several
   (`services`, from the sandbox library). The sandbox answers the apps' own
   APIs, so it needs no named tool and none is guessed for it. */
export const sandboxScenario = (row) => !!row?.twinService || (Array.isArray(row?.services) && row.services.length > 0);

export const inferTools = (row, tools = []) => {
  if (!tools.length) return [];
  const text = textOf(row).toLowerCase();
  const named = tools.filter((t) => text.includes(t.name)).map((t) => t.name);
  if (named.length) return named.slice(0, 3);

  const words = new Set(tokens(text));
  const perTool = tools.map((t) => ({
    name: t.name,
    nameWords: new Set(tokens(t.name.replace(/_/g, " "))),
    descWords: new Set(tokens(t.desc)),
  }));
  const spread = new Map();
  perTool.forEach((t) => new Set([...t.nameWords, ...t.descWords]).forEach((w) => spread.set(w, (spread.get(w) || 0) + 1)));
  /* A word only one tool uses points straight at it; one two tools share
     points at both, half as hard. */
  const weight = (w) => (!words.has(w) ? 0 : spread.get(w) === 1 ? 2 : spread.get(w) === 2 ? 1 : 0);

  return perTool
    .map((t) => ({
      name: t.name,
      score: [...t.nameWords].reduce((a, w) => a + weight(w) * 2, 0)
        + [...t.descWords].reduce((a, w) => a + weight(w), 0),
    }))
    /* One stray shared word is not enough to say a scenario needs a tool. */
    .filter((t) => t.score >= 2)
    .sort((a, b) => b.score - a.score)
    /* A second tool only when it is nearly as clearly meant as the first. */
    .filter((t, i, arr) => i === 0 || t.score >= arr[0].score * 0.5)
    .slice(0, 2)
    .map((t) => t.name);
};
