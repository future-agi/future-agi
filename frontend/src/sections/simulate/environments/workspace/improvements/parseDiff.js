// A unified diff (GET …/candidates/{id}/patch/, text/x-diff) as files of
// typed lines. Hunk headers and the a/ b/ preamble are dropped.
export function parseDiff(text) {
  const files = [];
  let current = null;
  let oldFile = "";
  (text || "").split("\n").forEach((raw) => {
    if (raw.startsWith("diff --git ")) {
      current = null;
      return;
    }
    if (raw.startsWith("--- ")) {
      // A deleted file's "+++" is /dev/null; its real path is only here.
      oldFile = raw.slice(4).replace(/^a\//, "");
      if (current) return;
    }
    if (raw.startsWith("+++ ")) {
      const next = raw.slice(4).replace(/^b\//, "");
      current = { file: next === "/dev/null" ? oldFile : next, lines: [] };
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
