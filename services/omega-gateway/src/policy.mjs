// Public policy surface, kept at Squad's import path (src/policy.mjs) so the
// ported Squad tests and callers keep working after the split into modules.
export { isAllowedPath, isAllowedRoute, isBundleRoute } from "./routes.mjs";
export { loadConfig, normalizeRemoteBaseUrl, parseAllowedOrigins } from "./config.mjs";
