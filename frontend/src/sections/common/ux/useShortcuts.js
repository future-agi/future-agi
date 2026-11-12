// useShortcuts (R3 G0 §B5): the one way a screen binds keys. Lanes pass a
// surface (keymap.js SURFACES) and handlers by action name; this resolves
// events with keymap.actionForEvent and the R3 focus guard, and registers
// the surface as current so `?` opens the sheet with it first.
//
//   useShortcuts("grid", { next: goNext, prev: goPrev, open: openRow,
//     density: cycleDensity, search: focusSearch }, { enabled: !panelOpen });
//
// Rules: letters never fire while typing (text fields, selects, menus,
// dialogs other than the sheet) but DO fire after clicking a button, tab or
// chip; arrows never fire inside an AG Grid (the grid owns them); Enter and
// Space on a focused button / link / tab activate it, not the shortcut;
// Cmd/Ctrl keys fire from anywhere except an open palette or sheet.
import { useEffect, useRef } from "react";
import { create } from "zustand";
import { surfaceForRoute } from "./routeSurface";
import { actionsForEvent, isArrowKey } from "src/utils/keymap";
import {
  isActivatable,
  isInGrid,
  isSafeForSingleKey,
  isTextInput,
} from "src/utils/keyboard-guards";

let seq = 0;

/**
 * R4: actions R3 screens handle outside useShortcuts (AG Grid cellKeyDown,
 * the Matrix's own onKeyDown). They count as live whenever the surface is
 * registered, so the `?` sheet keeps listing them. New screens pass
 * `opts.native` instead of growing this map.
 */
export const NATIVE_LIVE_ACTIONS = Object.freeze({
  grid: ["open"],
  matrix: ["cellLeft", "cellRight", "cellUp", "cellDown", "open"],
  // annotate/use-keyboard-shortcuts.js + label-panel.jsx (`N` notes).
  review: ["next", "prev", "submit", "skip", "escape", "help", "notes"],
});

// Live actions of `register: false` bindings (ColumnsMenu's `C`, global
// Esc): they add to a surface's live set without becoming its current one.
const extraLive = new Map();

/**
 * Global keyboard state: the surface stack (last = current), the `?` sheet
 * and the ⌘K palette. Zustand; read with selectors.
 */
export const useShortcutStore = create((set, get) => ({
  stack: [],
  sheet: { open: false, tab: "shortcuts", query: "", surface: null },
  paletteOpen: false,
  /**
   * `unavailable`: optional getter → `{action: reason}` (greyed in the sheet).
   * `live` (R4): optional getter → the action names this registration has a
   * handler for; the sheet lists only live actions of a surface whose every
   * registration reports them (useRowKeyboardNav does not: all rows show).
   */
  push: (surface, unavailable, live) => {
    seq += 1;
    const token = `${surface}#${seq}`;
    set((s) => ({
      stack: [...s.stack, { surface, token, unavailable, live }],
    }));
    return token;
  },
  pop: (token) =>
    set((s) => ({ stack: s.stack.filter((x) => x.token !== token) })),
  current: () => {
    const { stack } = get();
    return stack.length ? stack[stack.length - 1].surface : null;
  },
  /** Open the `?` sheet: `{tab: "shortcuts"|"glossary", query, surface}`. */
  openSheet: (opts = {}) =>
    set((s) => ({
      sheet: {
        open: true,
        tab: opts.tab ?? "shortcuts",
        query: opts.query ?? "",
        surface:
          opts.surface ??
          s.stack[s.stack.length - 1]?.surface ??
          (typeof window !== "undefined"
            ? surfaceForRoute(window.location.pathname, window.location.search)
            : null),
      },
    })),
  closeSheet: () => set((s) => ({ sheet: { ...s.sheet, open: false } })),
  setPaletteOpen: (open) => set({ paletteOpen: Boolean(open) }),
}));

/** The current surface (top of the stack), reactive. */
export const useCurrentSurface = () =>
  useShortcutStore((s) =>
    s.stack.length ? s.stack[s.stack.length - 1].surface : null,
  );

/**
 * Keys the topmost screen bound to `surface` says do nothing right now, as
 * `{action: reason}` for a real data reason (e.g. `{diffOnly: "Needs two
 * lanes"}`; never a mode); `{}` when none.
 * Read when the sheet renders (not reactive).
 */
export function unavailableFor(surface) {
  const { stack } = useShortcutStore.getState();
  const out = {};
  // Every registration of the surface; the topmost wins a clash.
  stack
    .filter((x) => x.surface === surface)
    .forEach((x) => Object.assign(out, x.unavailable?.() ?? {}));
  return out;
}

/**
 * R4: the actions with a live handler on `surface` (union of its
 * registrations), or null when unknown (no registration, or one that does
 * not report its handlers): then the sheet lists every action as before.
 * Read when the sheet renders (not reactive).
 * @returns {Set<string>|null}
 */
export function liveActionsFor(surface) {
  const entries = useShortcutStore
    .getState()
    .stack.filter((x) => x.surface === surface);
  if (!entries.length || entries.some((x) => typeof x.live !== "function"))
    return null;
  const set = new Set(NATIVE_LIVE_ACTIONS[surface] ?? []);
  entries.forEach((x) => (x.live() ?? []).forEach((a) => set.add(a)));
  extraLive.forEach((x) => {
    if (x.surface === surface) (x.live() ?? []).forEach((a) => set.add(a));
  });
  return set;
}

/** Open the `?` sheet from anywhere (palette, help buttons). */
export const openShortcutSheet = (opts) =>
  useShortcutStore.getState().openSheet(opts);

/** True while the palette or the sheet is open (keys belong to them). */
export const globalOverlayOpen = () => {
  const s = useShortcutStore.getState();
  return s.paletteOpen || s.sheet.open;
};

/**
 * Should a keydown reach shortcut handlers? Exported for tests.
 * @param {KeyboardEvent} event
 * @param {Element|null} focused
 */
export function shortcutAllowed(event, focused) {
  if (!event || event.defaultPrevented || event.isComposing) return false;
  if (globalOverlayOpen()) return false;
  const modded = event.metaKey || event.ctrlKey;
  if (modded) return true;
  if (isTextInput(focused)) return false;
  if (!isSafeForSingleKey(focused)) return false;
  if (isArrowKey(event) && isInGrid(focused)) return false;
  if ((event.key === "Enter" || event.key === " ") && isActivatable(focused))
    return false;
  return true;
}

/**
 * Bind shortcuts for a surface.
 * @param {string} surface  keymap surface (`grid`, `matrix`, `taskCompare`…)
 * @param {Object<string, ((e: KeyboardEvent) => void)|{run: Function,
 *   when?: () => boolean, reason?: string}>} handlers  by action. R4: an
 *   entry may be `{run, when, reason}`; while `when()` is false the key does
 *   nothing and the `?` sheet greys it with `reason` (`Needs an image lens`).
 *   When one key maps to several actions on the surface (`I` = `inspect` /
 *   `inspectNode`), the first one with a live handler fires.
 *   `opts.native` (R4): actions this screen handles outside useShortcuts
 *   (an AG Grid cellKeyDown, a widget's onKeyDown) so the sheet lists them.
 * @param {{enabled?: boolean, register?: boolean,
 *   unavailable?: Object<string, string>}} [opts]  `register:false` binds
 *   without becoming the sheet's current surface; `unavailable` names keys
 *   that do nothing right now for a data reason, with a short reason the `?`
 *   sheet shows beside the greyed row (`{diffOnly: "Needs two lanes"}`)
 */
/** A handler entry → `{run, when, reason}` (null when not callable). */
export function handlerEntry(h) {
  if (typeof h === "function") return { run: h, when: null, reason: null };
  if (h && typeof h.run === "function")
    return {
      run: h.run,
      when: typeof h.when === "function" ? h.when : null,
      reason: h.reason ?? null,
    };
  return null;
}

/** `{action: reason}` for handler entries whose `when()` is false now. */
export function blockedByWhen(handlers) {
  const out = {};
  Object.entries(handlers ?? {}).forEach(([action, h]) => {
    const e = handlerEntry(h);
    if (e?.when && !e.when()) out[action] = e.reason ?? "Not available here";
  });
  return out;
}

export default function useShortcuts(surface, handlers, opts = {}) {
  const { enabled = true, register = true } = opts;
  const ref = useRef(handlers);
  ref.current = handlers;
  const offRef = useRef(opts.unavailable);
  offRef.current = opts.unavailable;

  const nativeRef = useRef(opts.native);
  nativeRef.current = opts.native;
  useEffect(() => {
    if (!enabled) return undefined;
    const live = () => [
      ...Object.keys(ref.current ?? {}).filter((a) =>
        handlerEntry(ref.current[a]),
      ),
      ...(nativeRef.current ?? []),
    ];
    if (!register) {
      seq += 1;
      const key = `extra#${seq}`;
      extraLive.set(key, { surface, live });
      return () => extraLive.delete(key);
    }
    const token = useShortcutStore
      .getState()
      .push(
        surface,
        () => ({ ...blockedByWhen(ref.current), ...(offRef.current ?? {}) }),
        live,
      );
    return () => useShortcutStore.getState().pop(token);
  }, [surface, enabled, register]);

  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (event) => {
      const focused =
        typeof document !== "undefined" ? document.activeElement : null;
      if (!shortcutAllowed(event, focused)) return;
      const entry = actionsForEvent(event, surface)
        .map((a) => handlerEntry(ref.current?.[a]))
        .find(Boolean);
      if (!entry || (entry.when && !entry.when())) return;
      event.preventDefault();
      entry.run(event);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [surface, enabled]);
}

export { useShortcuts };
