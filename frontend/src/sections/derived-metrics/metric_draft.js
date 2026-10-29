// Scoped recovery is a second line of protection alongside the feature-local
// navigation guard. Never accept a recovery destination outside Metrics.
const keyFor = (scope) => `metric-definition-draft:v1:${scope}`;
const metricPath = "/dashboard/evaluations/metrics";
const normalizeLocation = (location) =>
  location?.pathname === `${metricPath}/`
    ? { ...location, pathname: metricPath }
    : location;
export function readMetricDraft(scope) {
  try {
    const value = JSON.parse(localStorage.getItem(keyFor(scope)) || "null");
    const location = normalizeLocation(value?.location);
    return value?.scope === scope &&
      ["new", "edit"].includes(value.mode) &&
      typeof value.form?.name === "string" &&
      typeof value.form?.formula === "string" &&
      Array.isArray(value.form?.inputs) &&
      (location?.pathname === metricPath ||
        /^\/dashboard\/evaluations\/metrics\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/?$/i.test(
          location?.pathname || "",
        ))
      ? { ...value, location }
      : null;
  } catch {
    return null;
  }
}
export function writeMetricDraft(scope, value) {
  try {
    localStorage.setItem(
      keyFor(scope),
      JSON.stringify({
        ...value,
        location: normalizeLocation(value.location),
        scope,
      }),
    );
    return true;
  } catch {
    return false;
  }
}
export function updateMetricDraft(scope, token, patch) {
  const current = readMetricDraft(scope);
  return current?.token === token
    ? writeMetricDraft(scope, { ...current, ...patch })
    : false;
}
export function clearMetricDraft(scope, token) {
  try {
    if (token && readMetricDraft(scope)?.token !== token) return true;
    localStorage.removeItem(keyFor(scope));
    return true;
  } catch {
    return false;
  }
}
