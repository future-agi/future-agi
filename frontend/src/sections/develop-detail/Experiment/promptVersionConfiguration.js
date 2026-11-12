const isObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

// Match the backend resolver: legacy snapshots use only the first list entry;
// malformed snapshots/configurations do not supply version settings.
export function promptVersionConfiguration(snapshot) {
  const first = Array.isArray(snapshot) ? snapshot[0] : snapshot;
  const configuration = isObject(first) ? first.configuration : null;
  return isObject(configuration) ? configuration : {};
}
