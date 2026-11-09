// IT-R2 G2 P1 (rule 14): an empty search or framework filter says what is
// missing and offers the button that adds it — `CrewAI has not run on this
// dataset. [Add a CrewAI run]` — instead of `No runs match "crewai"`. Pure.
import { searchCatalog } from "src/sections/common/RunSources/catalog";

const norm = (s) =>
  String(s ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");

/**
 * The catalog entry a query names (a framework, harness or voice platform),
 * when it names one closely: `crewai` → CrewAI, `openai agents` → OpenAI
 * Agents SDK. Null for a model, a run id or free text.
 * @param {string} query
 * @returns {{id: string, name: string}|null}
 */
export function catalogEntryNamed(query) {
  const q = norm(query);
  if (q.length < 3) return null;
  const hit = (searchCatalog(query) ?? []).find((e) => {
    const names = [e.name, e.short, ...(e.aliases ?? [])].map(norm);
    return names.some((n) => n && (n === q || n.startsWith(q)));
  });
  return hit ? { id: hit.id, name: hit.name } : null;
}

// L7c g2 (entry:leaderboard-not-run-here): the article follows the sound:
// `an HTTP endpoint`, `an MCP server tool`, `an A2A agent card`, `a CrewAI`.
const VOWEL_SOUND_LETTERS = "AEFHILMNORSX";
export function articleOf(name) {
  const word =
    String(name ?? "")
      .trim()
      .split(/\s+/)[0] ?? "";
  if (/^[aeiou]/i.test(word)) return "an";
  const acronym =
    word.length > 1 && word === word.toUpperCase() && /[A-Z]/.test(word[0]);
  return acronym && VOWEL_SOUND_LETTERS.includes(word[0]) ? "an" : "a";
}

/**
 * `{text, action, href}` for an empty result, or null when the query does
 * not name a framework / harness. `href` opens New experiment on that entry
 * (`new_experiment=1&entry=<id>`, the drawer's deep link).
 * @param {string} query
 * @param {{datasetId: string, datasetName?: string}} o
 */
export function notRunHere(query, { datasetId, datasetName } = {}) {
  const entry = catalogEntryNamed(query);
  if (!entry || !datasetId) return null;
  return {
    entry,
    text: `${entry.name} has not run on ${datasetName ?? "this dataset"}.`,
    action: `Add ${articleOf(entry.name)} ${entry.name} run`,
    href: `/dashboard/develop/${datasetId}?tab=experiments&new_experiment=1&entry=${encodeURIComponent(entry.id)}`,
  };
}
