// A unified diff (GET …/candidates/{id}/patch/, text/x-diff) as files of
// typed lines. Hunk headers and the a/ b/ preamble are dropped.
export function parseDiff(text) {
  const files = [];
  let current = null;
  (text || "").split("\n").forEach((raw) => {
    if (raw.startsWith("diff --git ")) {
      current = null;
      return;
    }
    if (raw.startsWith("+++ ")) {
      current = { file: raw.slice(4).replace(/^b\//, ""), lines: [] };
      files.push(current);
      return;
    }
    if (!current || raw.startsWith("--- ") || raw.startsWith("@@")) return;
    if (raw.startsWith("+"))
      current.lines.push({ type: "add", text: raw.slice(1) });
    else if (raw.startsWith("-"))
      current.lines.push({ type: "remove", text: raw.slice(1) });
    else if (raw.startsWith(" "))
      current.lines.push({ type: "ctx", text: raw.slice(1) });
  });
  return files;
}
