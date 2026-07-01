// Pure helpers for the dataset variable-mapping column picker (ColumnTreeSelect).
// Kept dependency-free so the tree-building logic is unit-testable without the
// heavy DatasetTestMode component graph.

// Parse flat column-path strings into the nested node tree the picker renders.
// "col" → leaf; "col.a.b" → nested; "col[0].x" → array-indexed nested.
// Each node is { id, label, path, children }; shared prefixes merge into one node.
export function buildTree(columnNames) {
  const roots = [];
  const nodeMap = {}; // path → node

  const getOrCreate = (path, label, parentList) => {
    if (nodeMap[path]) return nodeMap[path];
    const node = { id: path, label, path, children: [] };
    nodeMap[path] = node;
    parentList.push(node);
    return node;
  };

  columnNames.forEach((fullPath) => {
    // Split into segments: "col.a.b" → ["col","a","b"], "col[0].x" → ["col","[0]","x"]
    const segments = [];
    let current = "";
    for (let i = 0; i < fullPath.length; i++) {
      const ch = fullPath[i];
      if (ch === ".") {
        if (current) segments.push(current);
        current = "";
      } else if (ch === "[") {
        if (current) segments.push(current);
        current = "[";
      } else if (ch === "]") {
        current += "]";
        segments.push(current);
        current = "";
      } else {
        current += ch;
      }
    }
    if (current) segments.push(current);

    if (segments.length === 1) {
      getOrCreate(fullPath, segments[0], roots);
    } else {
      // Walk segments, creating intermediate nodes
      let parentList = roots;
      let builtPath = "";
      for (let i = 0; i < segments.length; i++) {
        const sep = i === 0 ? "" : segments[i].startsWith("[") ? "" : ".";
        builtPath += sep + segments[i];
        const node = getOrCreate(builtPath, segments[i], parentList);
        parentList = node.children;
      }
    }
  });
  return roots;
}
