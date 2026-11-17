// How the experiment Summary names runs and evaluations: by the names the
// Data tab shows, never by an internal id.

const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// The trimmed text, or "" for a missing value or one that is only an id.
const shown = (value) => {
  const text = typeof value === "string" ? value.trim() : "";
  return ID.test(text) ? "" : text;
};

/**
 * `Run B-gpt-4o-mini (#2)`: the run's name with its number, as the saved
 * ranking names it. The analysis read labels a run without a number by its
 * id; that id is never shown. A run with neither is `Unnamed run`.
 */
export function summaryRunName(run) {
  const name = shown(run?.name);
  const label = shown(run?.label);
  if (name && label && name !== label) return `${name} (${label})`;
  return name || label || "Unnamed run";
}

/** The evaluation's name; a name that is only an id reads `Unnamed evaluation`. */
export function summaryEvaluationName(metric) {
  return shown(metric?.name) || "Unnamed evaluation";
}
