export const validateGraphConnectivity = (nodes, edges) => {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return { orphanNames: [], orphanIds: [], noStartNode: false };
  }
  const startIds = nodes
    .filter((node) => node?.data?.isStart)
    .map((node) => node.id);
  if (startIds.length === 0) {
    return { orphanNames: [], orphanIds: [], noStartNode: true };
  }
  const outgoing = new Map();
  edges?.forEach((edge) => {
    if (!outgoing.has(edge.source)) {
      outgoing.set(edge.source, []);
    }
    outgoing.get(edge.source).push(edge.target);
  });
  const visited = new Set(startIds);
  const queue = [...startIds];
  while (queue.length > 0) {
    const current = queue.shift();
    (outgoing.get(current) || []).forEach((target) => {
      if (!visited.has(target)) {
        visited.add(target);
        queue.push(target);
      }
    });
  }
  const orphans = nodes.filter(
    (node) => !node?.data?.isGlobal && !visited.has(node.id),
  );
  return {
    orphanNames: orphans.map((node) => node?.data?.name || node.id),
    orphanIds: orphans.map((node) => node.id),
    noStartNode: false,
  };
};
