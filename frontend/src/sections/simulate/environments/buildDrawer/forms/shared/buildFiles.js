// Files picked on a build form, kept as plain rows.

export const BUILD_FILES_ACCEPT = ".csv,.tsv,.json,.jsonl,.xlsx,.txt,.md,.pdf";

export const extOf = (name) =>
  String(name || "")
    .split(".")
    .pop()
    .toLowerCase();

/** Picked files as plain rows. The same name again replaces the earlier row. */
export function mergeBuildFiles(prev, list) {
  const added = Array.from(list || []).map((f) => ({
    id: `${f.name}-${f.size}-${f.lastModified}`,
    name: f.name,
    size: f.size || 0,
    type: f.type || "",
  }));
  return [
    ...prev.filter((f) => !added.some((a) => a.name === f.name)),
    ...added,
  ];
}
