export function folderPickAfterMutation(pick, change) {
  if (pick?.kind !== "folder" || !change?.from) return pick;
  if (pick.path !== change.from && !pick.path.startsWith(`${change.from}/`))
    return pick;
  if (change.action === "remove") return { kind: "none" };
  return {
    kind: "folder",
    path: change.to + pick.path.slice(change.from.length),
  };
}
