// Match update-config's collapse_stored_fallback_aliases before diffing an
// editor draft. Otherwise a canonical patch over camel-only storage omits the
// null entries needed to delete removed primaries. Canonical values win when
// both spellings exist, even when the canonical map is empty or null.
export function normalizeFallbackRouting(routing) {
  if (!Object.prototype.hasOwnProperty.call(routing, "modelFallbacks")) {
    return routing;
  }
  const { modelFallbacks, ...canonical } = routing;
  return { model_fallbacks: modelFallbacks, ...canonical };
}
