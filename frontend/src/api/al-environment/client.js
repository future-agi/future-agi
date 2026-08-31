import axios from "axios";
import appAxios from "src/utils/axios";
import { HOST_API } from "src/config-global";

/**
 * On the platform the harness is reached through our own backend, which authenticates the
 * call and proxies it verbatim: /simulate/harness/<path> → harness /api/<path>. For local
 * development you can talk to the harness directly instead.
 *
 * One config value covers both, because the base replaces the prefix rather than sitting in
 * front of it — everything after it is byte-identical:
 *   platform  VITE_ALK_API_BASE unset  → <api host>/simulate/harness
 *   local     VITE_ALK_API_BASE=http://localhost:8777/api
 */
export const ALK_PROXY_PATH = "/simulate/harness";

/**
 * The proxy lives on the API host, not on ours. Every deployment serves the two from different
 * origins — dev.futureagi.com against dev.api.futureagi.com, and localhost:3000 against
 * localhost:8000 — so a bare path would resolve against the page and never reach the backend.
 */
export const alkBaseUrl = (env = {}, host = HOST_API) =>
  ((env.VITE_ALK_API_BASE || "").trim() || `${host || ""}${ALK_PROXY_PATH}`).replace(/\/+$/, "");

/**
 * Whether this base bypasses our backend. Decided by where it points rather than by whether it
 * is absolute: the proxied base is absolute too, and reading the scheme instead would strip the
 * auth headers off exactly the calls that need them.
 */
export const isDirectToHarness = (base) => !String(base || "").includes(ALK_PROXY_PATH);

/** The headers the app maintains for every authenticated call. */
export const AUTH_HEADERS = ["Authorization", "X-Organization-Id", "X-Workspace-Id"];

/**
 * Deliberately a separate instance from src/utils/axios: that one asserts every request and
 * response against the generated OpenAPI contract, and the proxied harness routes are not in
 * it. It also redirects to login on 401, which must not be triggered by a harness error.
 *
 * Auth is borrowed from it instead of reimplemented, so a token refresh or an organisation
 * switch applies here too without this file knowing how any of that works.
 */
const alkAxios = axios.create({
  baseURL: alkBaseUrl(import.meta.env),
  headers: { "Content-Type": "application/json" },
});

export const applyAuth = (config, base, shared) => {
  if (isDirectToHarness(base)) return config;
  AUTH_HEADERS.forEach((header) => {
    const value = shared?.[header];
    if (value) config.headers[header] = value;
  });
  return config;
};

alkAxios.interceptors.request.use((config) =>
  applyAuth(config, config.baseURL || "", appAxios.defaults.headers.common)
);

export default alkAxios;
