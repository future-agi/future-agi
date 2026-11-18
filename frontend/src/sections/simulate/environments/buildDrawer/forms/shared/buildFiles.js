// Files picked on a build form, kept as plain rows.

// A build that asks what to test waits for at least this many characters.
export const BUILD_TEST_MIN = 10;

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

// How the builder will likely read a file, from its name alone. Drag and drop
// skips `accept`, so a type outside it says it can't be read.
export function readAsOf(name, accept = BUILD_FILES_ACCEPT) {
  const ext = extOf(name);
  const lower = name.toLowerCase();
  if (!accept.split(",").includes(`.${ext}`)) return null;
  if (/expected|outcome|answer|golden/.test(lower)) return "Grader answers";
  if (["csv", "tsv", "json", "jsonl", "xlsx"].includes(ext)) return "Records";
  const policyish =
    /polic|terms|rule|sop|procedure|guideline|sla|returns?|refund/.test(lower);
  const reference =
    /chat|conversation|transcript|guide|faq|handbook|reference|catalog|macro|manual|readme|notes?\b|changelog/.test(
      lower,
    ) ||
    (["md", "txt"].includes(ext) && !policyish);
  return reference ? "Background" : "Rules";
}
