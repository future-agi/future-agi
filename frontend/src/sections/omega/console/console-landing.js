// Two ways into the Console carry a parameter from somewhere else. Each is
// checked here before the frame is built, and anything that does not pass is
// dropped from the URL rather than handed to the Console.
//
// - The Hub's "Start in Omega Console" (Hub SPEC §7.4):
//     /dashboard/console?hub=<publisher>/<name>@<version>
//   becomes the Console route /templates/import?hub=<ref>. The reference must
//   be a pinned one in the Hub's own grammar (omega_hub/services/refs.py REF,
//   with the version required, as the Hub's Use this menu always pins one).
//   On only with VITE_OMEGA_HUB_CONSOLE, the flag that also shows the menu
//   item, so the receiving side is never on without the sending side.
// - A public receipt's "Remix this agent" (CP T2, wave 3):
//     /dashboard/console/remix/<share token>
//   stays the Console route /remix/<token>. The token must have the shape EE's
//   replay-share-route.ts signs (rpl_v1.<claim>.<signature>). Off unless
//   VITE_OMEGA_CONSOLE_REMIX is set: the public loop launches dark (CP D12,
//   READINESS OD-01), so with the flag off the link lands on the Console home.
//
// Both flags are read like VITE_OMEGA_CONSOLE: runtime config first, then
// the build.
import { CONSOLE_BASE_PATH } from "./console-config";

const SEMVER_PART = "(0|[1-9][0-9]{0,5})";
export const HUB_REF = new RegExp(
  `^[a-z][a-z0-9-]{1,38}/[a-z][a-z0-9-]{2,39}@${SEMVER_PART}\\.${SEMVER_PART}\\.${SEMVER_PART}$`,
);
export const REMIX_TOKEN =
  /^rpl_v1\.[A-Za-z0-9_-]{1,1800}\.[A-Za-z0-9_-]{16,128}$/;

export const HUB_IMPORT_ROUTE = "/templates/import";
const REMIX_PREFIX = "/remix";
const ON = new Set(["1", "true"]);

export function readLandingFlag(name, runtimeConfig, buildEnv) {
  const runtimeValue = runtimeConfig?.[name];
  const raw =
    runtimeValue !== undefined && runtimeValue !== null && runtimeValue !== ""
      ? runtimeValue
      : buildEnv?.[name];
  return raw !== undefined && raw !== null && ON.has(String(raw).trim());
}

export function landingFlags() {
  const runtime =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  const build = import.meta.env || {};
  return {
    hub: readLandingFlag("VITE_OMEGA_HUB_CONSOLE", runtime, build),
    remix: readLandingFlag("VITE_OMEGA_CONSOLE_REMIX", runtime, build),
  };
}

const withQuery = (path, params) => {
  const query = params.toString();
  return query ? `${path}?${query}` : path;
};

/**
 * Where the parent URL must go before the frame is built, or null when it is
 * already where it should be.
 * @param {string} pathname
 * @param {string} search
 * @param {{ hub: boolean, remix: boolean }} flags
 * @returns {string | null}
 */
export function consoleLandingRedirect(pathname, search, flags) {
  const base = CONSOLE_BASE_PATH;
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : null;
  if (rest === null) return null;
  const route = rest.replace(/\/+$/, "") || "/";
  const params = new URLSearchParams(search);

  if (params.has("hub") && (route === "/" || route === HUB_IMPORT_ROUTE)) {
    const refs = params.getAll("hub");
    params.delete("hub");
    const ref = refs.length === 1 ? refs[0] : null;
    if (flags.hub && ref && HUB_REF.test(ref)) {
      params.set("hub", ref);
      const target = withQuery(`${base}${HUB_IMPORT_ROUTE}`, params);
      return target === `${pathname}${search}` ? null : target;
    }
    return withQuery(route === "/" ? base : `${base}${route}`, params);
  }

  if (route === REMIX_PREFIX || route.startsWith(`${REMIX_PREFIX}/`)) {
    const token = route.slice(REMIX_PREFIX.length + 1);
    const valid = !token.includes("/") && REMIX_TOKEN.test(token);
    return flags.remix && valid ? null : base;
  }
  return null;
}
