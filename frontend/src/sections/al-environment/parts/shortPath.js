/**
 * The harness answers with absolute paths on the machine it runs on, e.g.
 * /Users/someone/Documents/agent-learning-kit/artifacts/sessions/<id>/scenarios/<name>.
 * Everything before `artifacts/` is that machine's business, not the reader's, and on the
 * platform it is a container path nobody can act on — so show the part that identifies the
 * folder and drop the rest.
 */
export const shortPath = (path) => {
  if (!path) return "";
  const at = path.indexOf("artifacts/");
  if (at !== -1) return path.slice(at);
  // No anchor to cut at — keep the tail, which is the part that names the thing.
  const parts = path.split("/").filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-3).join("/")}` : path;
};
