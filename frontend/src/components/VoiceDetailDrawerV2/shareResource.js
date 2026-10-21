const SIMULATE_ORIGINS = new Set(["simulate", "agent-definition"]);

// Simulation calls are shared by their CallExecution id — a simulate row's
// trace_id (when present) belongs to the agent's Observe project, not to the
// call the user is looking at. Observe calls are traces.
export function isSimulationCall(data) {
  return data?.module === "simulate" || SIMULATE_ORIGINS.has(data?.origin);
}

export function shareResourceFor(data) {
  if (isSimulationCall(data)) {
    return data.id
      ? { resourceType: "call_execution", resourceId: data.id }
      : null;
  }
  const id = data?.trace_id || data?.id;
  return id ? { resourceType: "trace", resourceId: id } : null;
}
