// URL = view state (R3 G0 §B8, F5, SVP-P1-7). Typed read/write of URL
// params with `replace`; defaults are omitted from the URL; every other
// param is kept. Reserved keys: `ui` and R2's `view`.
//
//   const [view, setView, resetView, isModified] = useViewUrlState({
//     density: "compact", q: "", failed: false, cols: [], page: 0,
//   });
//   setView({ density: "tall" });          // or setView((v) => ({...}))
import { useCallback, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";

// `ui` stays reserved forever (G-07): it was the Simple/Advanced mode, and
// old links still carry it (LegacyParamScrub drops it); never give it a new
// meaning. `view` is R2 Summary's.
export const RESERVED_VIEW_KEYS = Object.freeze(["ui", "view"]);

const typeOf = (v) =>
  Array.isArray(v)
    ? "array"
    : v === null || v === undefined
      ? "nullable"
      : typeof v;

/** Parse one raw param by the default's type (falls back to the default). */
export function parseViewValue(raw, def) {
  if (raw === null || raw === undefined) return def;
  switch (typeOf(def)) {
    case "boolean":
      return raw === "1" || raw === "true"
        ? true
        : raw === "0" || raw === "false"
          ? false
          : def;
    case "number": {
      const n = Number(raw);
      return raw !== "" && Number.isFinite(n) ? n : def;
    }
    case "array":
      return raw === "" ? [] : raw.split(",").map((s) => decodeURIComponent(s));
    default:
      return raw;
  }
}

/** Serialise a value; null = remove the param. */
export function serializeViewValue(value, def) {
  if (value === null || value === undefined) return null;
  switch (typeOf(def)) {
    case "boolean":
      return value ? "1" : "0";
    case "array":
      return (value ?? []).map((s) => encodeURIComponent(String(s))).join(",");
    default:
      return String(value);
  }
}

/** The live search: the browser's when it shows this route (a page may have
 * replaced params outside the router), else the router's (MemoryRouter). */
export const liveSearch = (location) =>
  typeof window !== "undefined" &&
  window.location.pathname === location.pathname
    ? window.location.search
    : location.search;

const same = (a, b) =>
  Array.isArray(a) || Array.isArray(b)
    ? JSON.stringify(a ?? []) === JSON.stringify(b ?? [])
    : a === b;

/** The typed state for `defaults` read from a search string. */
export function readViewState(search, defaults) {
  const sp = new URLSearchParams(search ?? "");
  return Object.fromEntries(
    Object.entries(defaults).map(([k, def]) => [
      k,
      parseViewValue(sp.get(k), def),
    ]),
  );
}

/** `search` with `state` written (defaults removed, other params kept). */
export function writeViewState(search, state, defaults) {
  const sp = new URLSearchParams(search ?? "");
  Object.entries(defaults).forEach(([k, def]) => {
    if (!(k in state)) return;
    const v = state[k];
    if (same(v, def)) sp.delete(k);
    else {
      const raw = serializeViewValue(v, def);
      if (raw === null) sp.delete(k);
      else sp.set(k, raw);
    }
  });
  return sp.toString();
}

/** True when any key differs from its default. */
export const viewIsModified = (state, defaults) =>
  Object.entries(defaults).some(([k, def]) => !same(state[k], def));

/**
 * @param {Object<string, any>} defaults  key → default (its type types the
 *   param: boolean `1`/`0`, number, array as CSV, string, null)
 * @returns {[state: object, set: (patch|fn) => void, reset: () => void,
 *   isModified: boolean]}
 */
export function useViewUrlState(defaults) {
  const location = useLocation();
  const navigate = useNavigate();
  const keyList = Object.keys(defaults).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const defs = useMemo(() => defaults, [keyList]);
  if (import.meta.env?.DEV)
    RESERVED_VIEW_KEYS.forEach((k) => {
      if (k in defs)
        // eslint-disable-next-line no-console
        console.warn(`useViewUrlState: "${k}" is reserved; use another key.`);
    });
  const state = useMemo(
    () => readViewState(location.search, defs),
    [location.search, defs],
  );
  const go = useCallback(
    (search) =>
      navigate(
        {
          pathname: location.pathname,
          search: search ? `?${search}` : "",
          hash: location.hash,
        },
        { replace: true, state: location.state },
      ),
    [navigate, location.pathname, location.hash, location.state],
  );
  const set = useCallback(
    (patch) => {
      const live = liveSearch(location);
      const current = readViewState(live, defs);
      const next = typeof patch === "function" ? patch(current) : patch;
      go(writeViewState(live, { ...current, ...next }, defs));
    },
    [defs, go, location],
  );
  const reset = useCallback(() => {
    go(writeViewState(liveSearch(location), defs, defs));
  }, [defs, go, location]);
  return [state, set, reset, viewIsModified(state, defs)];
}

export default useViewUrlState;
