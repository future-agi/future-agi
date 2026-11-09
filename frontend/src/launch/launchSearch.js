// L6 launch switch (entry:deep-link): the deep-link sanitizer. A query key
// that opens a hidden surface (the registry's `entries.search`) is removed,
// or replaced (`tab=summary` -> `tab=data`), before any page reads it. The
// keys a page uses for shown surfaces pass through untouched. Pure.
import { LAUNCH_SURFACES, matchesGlob } from "./launchSurfaces";
import { launchState, surfaceShownIn } from "./launchSwitch";

const keyHits = (rule, value) =>
  !rule.values || rule.values.includes(String(value));

/**
 * @param {string|URLSearchParams} search  `?a=1&b=2` (leading ? optional)
 * @param {string} pathname  the page; a rule applies only where `on` matches
 * @returns {{search: string, changed: boolean, surfaces: string[],
 *   dropped: string[]}}  `search` keeps the leading `?` (or is "").
 */
export function sanitizeLaunchSearch(search, pathname, state = launchState()) {
  const params = new URLSearchParams(
    typeof search === "string" ? search : search?.toString?.() ?? "",
  );
  const out = { changed: false, surfaces: [], dropped: [] };
  if (!state.launch) {
    const s = params.toString();
    return { ...out, search: s ? `?${s}` : "" };
  }
  for (const surface of LAUNCH_SURFACES) {
    const rules = surface.entries?.search ?? [];
    if (!rules.length || surfaceShownIn(surface.id, state)) continue;
    for (const rule of rules) {
      if (!rule.on.test(String(pathname ?? ""))) continue;
      const keys = [...new Set(params.keys())].filter((key) =>
        matchesGlob(rule.key, key),
      );
      for (const key of keys) {
        const value = params.get(key);
        if (!keyHits(rule, value)) continue;
        if (rule.replace !== undefined) params.set(key, rule.replace);
        else params.delete(key);
        out.changed = true;
        out.dropped.push(`${key}=${value}`);
        if (!out.surfaces.includes(surface.id)) out.surfaces.push(surface.id);
      }
    }
  }
  const s = params.toString();
  return { ...out, search: s ? `?${s}` : "" };
}

/** Whether a URL query would open a hidden surface. */
export const searchOpensHidden = (search, pathname, state = launchState()) =>
  sanitizeLaunchSearch(search, pathname, state).changed;
