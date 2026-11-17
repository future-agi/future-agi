import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/** Legacy URL params the one view drops (G-03): the Simple/Advanced `ui`. */
export const LEGACY_PARAMS = Object.freeze(["ui"]);

/**
 * `search` without the legacy params, or null when there is nothing to
 * remove. Every other param keeps its value and order. Pure.
 */
export function scrubLegacyParams(search) {
  const sp = new URLSearchParams(search ?? "");
  if (!LEGACY_PARAMS.some((k) => sp.has(k))) return null;
  LEGACY_PARAMS.forEach((k) => sp.delete(k));
  return sp.toString();
}

/**
 * LegacyParamScrub — mounted once in the dashboard layout (replaces the
 * old mode sync). An old link with `?ui=simple|advanced` loads the one view
 * and the param is removed with `replace` (hash and router state kept).
 */
export default function LegacyParamScrub() {
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    const next = scrubLegacyParams(location.search);
    if (next === null) return;
    navigate(
      {
        pathname: location.pathname,
        search: next ? `?${next}` : "",
        hash: location.hash,
      },
      { replace: true, state: location.state },
    );
  }, [location, navigate]);
  return null;
}
