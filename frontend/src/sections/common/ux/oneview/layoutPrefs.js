// One-view layout persistence (ONE-VIEW-SPEC §3 "Persistence model", §3.1).
// Layout = what a user shaped on a surface: columns, open sections, outline
// rows, density, small extras. Per user, per surface, optionally per scope
// (dataset id, experiment id). localStorage key
// `fa.layout.<userId>.<surface>[.<scopeId>]`; JSON
// `{v: 1, columns, sections, outline, density, extras}`. Every read and
// write is wrapped: with storage blocked the screen renders the default.
//
//   const [prefs, patch, reset] = useLayoutPrefs("results", experimentId);
//   patch({ sections: { charts: false } });   // merged one level deep
//   patch((p) => ({ ...p, density: "tall" })); // or replace
//   reset(["columns"]);                        // or reset() everything
import { useCallback, useContext, useMemo } from "react";
import { create } from "zustand";
import { AuthContext } from "src/auth/context/jwt/auth-context";

export const LAYOUT_VERSION = 1;
export const PROTO_USER_ID = "proto-user";
/** Legacy Simple/Advanced key (`fa.ui.mode.<userId>`): deleted, never read. */
export const LEGACY_MODE_KEY_PREFIX = "fa.ui.mode.";

const OBJECT_FIELDS = ["columns", "sections", "outline", "extras"];

/** A fresh default layout (new objects every call). */
export const emptyLayout = () => ({
  v: LAYOUT_VERSION,
  columns: {},
  sections: {},
  outline: {},
  density: null,
  extras: {},
});

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** `fa.layout.<userId>.<surface>[.<scopeId>]` (`proto-user` without a user). */
export function layoutKey(userId, surface, scopeId) {
  const base = `fa.layout.${userId || PROTO_USER_ID}.${surface || "page"}`;
  return scopeId ? `${base}.${scopeId}` : base;
}

/** A stored value made safe: wrong version or shape → the default. */
export function normalizeLayout(value) {
  if (!isObj(value) || value.v !== LAYOUT_VERSION) return emptyLayout();
  const out = emptyLayout();
  OBJECT_FIELDS.forEach((f) => {
    if (isObj(value[f])) out[f] = value[f];
  });
  if (typeof value.density === "string") out.density = value.density;
  return out;
}

/** Read a layout; missing, unparsable, other version or blocked → default. */
export function readLayout(key) {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return emptyLayout();
    return normalizeLayout(JSON.parse(raw));
  } catch {
    return emptyLayout();
  }
}

/** True when nothing differs from the default (nothing worth storing). */
export function isEmptyLayout(value) {
  const v = normalizeLayout(value);
  return (
    v.density == null &&
    OBJECT_FIELDS.every((f) => Object.keys(v[f]).length === 0)
  );
}

/** Write (or remove, when empty). Returns false when storage is blocked. */
export function writeLayout(key, value) {
  try {
    if (isEmptyLayout(value)) window.localStorage.removeItem(key);
    else
      window.localStorage.setItem(
        key,
        JSON.stringify({ ...normalizeLayout(value), v: LAYOUT_VERSION }),
      );
    return true;
  } catch {
    return false;
  }
}

/**
 * Merge `b` over `a`: the object fields (`columns`, `sections`, `outline`,
 * `extras`) merge one level deep (a key set to `undefined` is removed),
 * `density` is replaced when `b` has it. Pure.
 */
export function mergeLayout(a, b) {
  const base = normalizeLayout(a?.v ? a : { ...a, v: LAYOUT_VERSION });
  if (!isObj(b)) return base;
  const out = { ...base };
  OBJECT_FIELDS.forEach((f) => {
    if (!(f in b)) return;
    if (!isObj(b[f])) {
      out[f] = {};
      return;
    }
    const merged = { ...base[f], ...b[f] };
    Object.keys(merged).forEach((k) => {
      if (merged[k] === undefined) delete merged[k];
    });
    out[f] = merged;
  });
  if ("density" in b) out.density = b.density ?? null;
  return out;
}

/** Delete every legacy `fa.ui.mode.*` key once. Returns how many went. */
export function removeLegacyModeKeys(storage) {
  try {
    const s = storage ?? window.localStorage;
    const doomed = [];
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k && k.startsWith(LEGACY_MODE_KEY_PREFIX)) doomed.push(k);
    }
    doomed.forEach((k) => s.removeItem(k));
    return doomed.length;
  } catch {
    return 0;
  }
}

// One-time cleanup on first import (ONE-VIEW-SPEC §6 phase 1 step 5).
if (typeof window !== "undefined") removeLegacyModeKeys();

/**
 * One store for every surface: two components of one surface share the
 * key, so they re-render together. `byKey[key]` is set on the first write;
 * until then readers fall back to storage.
 */
export const useLayoutStore = create((set) => ({
  byKey: {},
  put: (key, value) => set((s) => ({ byKey: { ...s.byKey, [key]: value } })),
}));

/** Current layout of a key (store, else storage). Not reactive. */
export const getLayout = (key) =>
  useLayoutStore.getState().byKey[key] ?? readLayout(key);

/** Write a layout for a key (store + storage). */
export function setLayout(key, value) {
  const next = normalizeLayout(value);
  useLayoutStore.getState().put(key, next);
  writeLayout(key, next);
  return next;
}

/** The signed-in user's id, else null (`proto-user` in keys). */
export function useLayoutUserId() {
  const ctx = useContext(AuthContext);
  return ctx?.user?.id ?? null;
}

/** The storage key of a surface for the current user. */
export function useLayoutKey(surface, scopeId) {
  const userId = useLayoutUserId();
  return layoutKey(userId, surface, scopeId);
}

/**
 * @param {string} surface  keymap-style surface (`results`, `list`, …)
 * @param {string} [scopeId]  dataset / experiment id
 * @returns {[prefs: object, patch: (partial|fn) => void,
 *   reset: (keys?: string[]) => void, key: string]}
 */
export function useLayoutPrefs(surface, scopeId) {
  const key = useLayoutKey(surface, scopeId);
  const stored = useLayoutStore((s) => s.byKey[key]);
  // Not in the store yet: read storage once per key.
  const fromStorage = useMemo(
    () => (stored ? null : readLayout(key)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, Boolean(stored)],
  );
  const prefs = stored ?? fromStorage;

  const patch = useCallback(
    (partial) => {
      const cur = getLayout(key);
      const next =
        typeof partial === "function"
          ? normalizeLayout({ ...partial(cur), v: LAYOUT_VERSION })
          : mergeLayout(cur, partial);
      setLayout(key, next);
    },
    [key],
  );

  const reset = useCallback(
    (keys) => {
      if (!keys?.length) {
        setLayout(key, emptyLayout());
        return;
      }
      const cur = getLayout(key);
      const fresh = emptyLayout();
      const next = { ...cur };
      keys.forEach((k) => {
        next[k] = fresh[k];
      });
      setLayout(key, next);
    },
    [key],
  );

  return [prefs, patch, reset, key];
}

export default useLayoutPrefs;
