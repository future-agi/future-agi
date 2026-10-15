// Keep execution-record identity aligned with the graph and detail inspector.
export function buildExecutedNodeTree(nodes = [], prefix = "") {
  if (!Array.isArray(nodes)) return [];
  return nodes.flatMap((node) => {
    const execution = node.nodeExecution ?? node.node_execution;
    const id = prefix ? `${prefix}__${node.id}` : node.id;
    const children = buildExecutedNodeTree(node.subGraph?.nodes, id);
    if (!execution && children.length === 0) return [];
    const durationSeconds = execution?.duration_seconds ?? execution?.durationSeconds;
    const duration = durationSeconds == null ? undefined : Number(durationSeconds) * 1000;
    return [{
      id,
      name: execution?.node_name ?? node.name ?? node.data?.label ?? id,
      type: execution?.node_type ?? node.type,
      ...(Number.isFinite(duration) && duration >= 0 ? { duration } : {}),
      ...(children.length ? { children } : {}),
    }];
  });
}
