// The tree, built from flat paths: folders first, then files, both by name.
// Each node carries its path relative to the agent's folder, so the tree can
// open the folders a selected file lives in.
export const buildTree = (files, subfolder) => {
  const root = { name: "", path: "", folders: new Map(), files: [] };
  const prefix = subfolder ? `${subfolder}/` : "";
  files.forEach((file) => {
    const relative = file.path.startsWith(prefix)
      ? file.path.slice(prefix.length)
      : file.path;
    const parts = relative.split("/");
    let node = root;
    parts.slice(0, -1).forEach((part) => {
      if (!node.folders.has(part)) {
        node.folders.set(part, {
          name: part,
          path: node.path ? `${node.path}/${part}` : part,
          folders: new Map(),
          files: [],
        });
      }
      node = node.folders.get(part);
    });
    node.files.push({ ...file, name: parts[parts.length - 1] });
  });
  return root;
};

// The folders (by relative path) that hold the selected file.
export const foldersOf = (selected, subfolder) => {
  if (!selected) return new Set();
  const prefix = subfolder ? `${subfolder}/` : "";
  const relative = selected.startsWith(prefix)
    ? selected.slice(prefix.length)
    : selected;
  const parts = relative.split("/").slice(0, -1);
  const open = new Set();
  parts.forEach((part, i) => open.add(parts.slice(0, i + 1).join("/")));
  return open;
};
