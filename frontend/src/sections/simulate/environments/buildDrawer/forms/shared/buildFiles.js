// Files picked on a build form, kept as plain rows.

// A build that asks what to test, or the flow to build from, waits for at
// least this many characters.
export const BUILD_TEST_MIN = 10;

export const BUILD_FILES_ACCEPT =
  ".csv,.tsv,.json,.jsonl,.xlsx,.txt,.md,.pdf,.docx";

export const extOf = (name) =>
  String(name || "")
    .split(".")
    .pop()
    .toLowerCase();

/**
 * Add file rows by name. A new name joins the others; the same name with
 * other contents replaces the earlier row in place; an identical copy (the
 * same id: name, size and modified time) is skipped. The note lists the
 * names in each group.
 */
export function mergeFileRows(existing, added) {
  const files = [...existing];
  const note = { added: [], replaced: [], skipped: [] };
  added.forEach((f) => {
    const at = files.findIndex((x) => x.name === f.name);
    if (at < 0) {
      files.push(f);
      note.added.push(f.name);
    } else if (files[at].id === f.id) {
      note.skipped.push(f.name);
    } else {
      files[at] = f;
      note.replaced.push(f.name);
    }
  });
  return { files, note };
}

/** Picked files as plain rows, merged into `prev` by name. */
export const mergeBuildFiles = (prev, list) =>
  mergeFileRows(
    prev,
    Array.from(list || []).map((f) => ({
      id: `${f.name}-${f.size}-${f.lastModified}`,
      name: f.name,
      size: f.size || 0,
      type: f.type || "",
    })),
  ).files;

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
