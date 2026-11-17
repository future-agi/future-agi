// L6 launch switch: where a hidden page's link lands in a launch build.
// DashboardRoutes asks launchRedirect(location) before it renders the
// Outlet: a route of a hidden surface lands on its fallback, and a query key
// that would open a hidden surface (`?new_experiment=1`, `?add=production`,
// `?tab=summary`) is dropped (launchSearch.js). Prototype builds and unit
// tests never redirect. Pure.
import { LAUNCH_SURFACES } from "./launchSurfaces";
import { launchState, surfaceShownIn } from "./launchSwitch";
import { sanitizeLaunchSearch } from "./launchSearch";

/** The hidden surface whose route matches `pathname`, with the match. */
export function hiddenRouteOf(pathname, state = launchState()) {
  if (!state.launch) return null;
  const p = String(pathname ?? "");
  for (const s of LAUNCH_SURFACES) {
    for (const r of s.entries?.routes ?? []) {
      const m = r.re.exec(p);
      if (m && !surfaceShownIn(s.id, state))
        return { surface: s.id, route: r, match: m };
    }
  }
  return null;
}

/** The fallback path for a hidden route match (`$1` = first capture). */
export const fallbackOf = ({ route, match }) =>
  route.fallback.replace("$1", match[1] ?? "");

/**
 * Where a launch build sends this location, or null to render it as is.
 * @param {{pathname: string, search?: string, hash?: string}} location
 * @returns {null|{to: string, surface: string}}
 */
export function launchRedirect(location, state = launchState()) {
  if (!state.launch || !location) return null;
  const hit = hiddenRouteOf(location.pathname, state);
  if (hit) return { to: fallbackOf(hit), surface: hit.surface };
  const clean = sanitizeLaunchSearch(
    location.search ?? "",
    location.pathname,
    state,
  );
  if (!clean.changed) return null;
  return {
    to: `${location.pathname}${clean.search}${location.hash ?? ""}`,
    surface: clean.surfaces[0],
  };
}
