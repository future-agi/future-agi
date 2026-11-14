// Dependency-free helpers for the column picker (ColumnTreeSelect), kept unit-testable.

// Parse flat column paths into a nested node tree; shared prefixes merge.
// "col" → leaf; "col.a.b" → nested; "col[0].x" → array-indexed.
// `isColumn` marks nodes whose path is an actual column (so a path that is both
// a scalar and a prefix — e.g. "status" + "status.code" — stays selectable).
export function buildTree(columnNames) {
  const roots = [];
  const nodeMap = {}; // path → node

  const getOrCreate = (path, label, parentList) => {
    if (nodeMap[path]) return nodeMap[path];
    const node = { id: path, label, path, children: [], isColumn: false };
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
      getOrCreate(fullPath, segments[0], roots).isColumn = true;
    } else {
      // Walk segments, creating intermediate nodes; the terminal one is the column.
      let parentList = roots;
      let builtPath = "";
      for (let i = 0; i < segments.length; i++) {
        const sep = i === 0 ? "" : segments[i].startsWith("[") ? "" : ".";
        builtPath += sep + segments[i];
        const node = getOrCreate(builtPath, segments[i], parentList);
        if (i === segments.length - 1) node.isColumn = true;
        parentList = node.children;
      }
    }
  });
  return roots;
}
