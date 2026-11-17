// Test-only history, not product API exceptions or generated service contracts.
// These licensing routes are no longer mounted by either URLconf. Their probes
// require explicit absence (404) and absence from both product path registries.
// A 404 here is never recorded as an authentication refusal.
export const HISTORICAL_BOUNDARY_PATHS = Object.freeze({
  "/usage/ee/licenses/": Object.freeze(["GET", "POST"]),
  "/usage/ee/licenses/{grant_id}/revoke/": Object.freeze(["POST"]),
});

export function historicalBoundaryPath(template, params = {}) {
  if (!Object.hasOwn(HISTORICAL_BOUNDARY_PATHS, template)) {
    throw new Error(`Not a retained historical boundary: ${template}`);
  }
  return template.replace(/\{([^}]+)\}/g, (_, name) => {
    const value = params[name];
    if (value === undefined || value === null || value === "") {
      throw new Error(`Missing historical boundary parameter: ${name}`);
    }
    return encodeURIComponent(String(value));
  });
}
